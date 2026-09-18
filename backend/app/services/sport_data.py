import logging
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.config import get_settings
from app.models.base import SportType

logger = logging.getLogger("dptv.sport_data")

# Display name shown in the "Create Live Sport Category" picker and used to name the category
# itself (e.g. "Live Rugby") - the single source of truth for which sports are supported.
SPORT_LABELS: dict[SportType, str] = {
    SportType.RUGBY: "Live Rugby",
}


@dataclass(frozen=True)
class Fixture:
    """One match, in whatever shape the provider returned it, normalized to what the matcher
    (see sport_refresh.py) actually needs - which team names to search for, and whether the
    match is currently live or already finished (neither means it hasn't kicked off yet).
    Provider-specific fields (scores, venue, ids) stay provider-specific; nothing downstream of
    fetch_fixtures() needs to know which sport or API produced this."""

    id: str
    competition: str
    kickoff: datetime
    home: str
    away: str
    is_live: bool
    is_finished: bool
    home_score: int | None = None
    away_score: int | None = None


_RUGBY_HOST = "rugby-live-data.p.rapidapi.com"

# The provider's own documented status enum (Fixture, Team In, First Half, Half Time, Second
# Half, Extra Time First Half, Extra Time Half Time, Extra Time Second Half, Shoot Out, Sudden
# Death, Result, Postponed, Abandoned, Cancelled) doesn't always match what it actually returns -
# "Not Started" shows up in real responses despite not being in that list. Classifying by keyword
# rather than exact match is what actually survives that drift: anything clearly pre-match or
# finished/dead is excluded, everything else (any of the "half"/"extra time"/"shoot out"/"sudden
# death" in-progress states, including ones not seen yet) counts as live.
_RUGBY_NOT_STARTED = {"fixture", "not started", "team in"}
_RUGBY_FINISHED_OR_DEAD = {"result", "postponed", "abandoned", "cancelled"}


def _classify_rugby_status(status: str) -> tuple[bool, bool]:
    """Returns (is_live, is_finished) - neither true means the fixture hasn't kicked off yet."""
    s = status.strip().lower()
    if s in _RUGBY_NOT_STARTED:
        return False, False
    if s in _RUGBY_FINISHED_OR_DEAD:
        return False, True
    return True, False


async def fetch_rugby_fixtures(target_date: date) -> list[Fixture]:
    settings = get_settings()
    if not settings.rapidapi_key:
        raise RuntimeError("DPTV_RAPIDAPI_KEY is not configured")

    url = f"https://{_RUGBY_HOST}/fixtures-by-date/{target_date.isoformat()}"
    headers = {"x-rapidapi-host": _RUGBY_HOST, "x-rapidapi-key": settings.rapidapi_key}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    fixtures: list[Fixture] = []
    for row in data.get("results", []):
        try:
            kickoff = datetime.fromisoformat(row["date"])
            is_live, is_finished = _classify_rugby_status(row.get("status", ""))
            fixtures.append(
                Fixture(
                    id=str(row["id"]),
                    competition=row.get("comp_name", ""),
                    kickoff=kickoff,
                    home=row["home"],
                    away=row["away"],
                    is_live=is_live,
                    is_finished=is_finished,
                    home_score=row.get("home_score"),
                    away_score=row.get("away_score"),
                )
            )
        except (KeyError, ValueError):
            logger.warning("Skipping malformed rugby fixture row: %r", row)
    return fixtures


_FETCHERS = {SportType.RUGBY: fetch_rugby_fixtures}


async def fetch_fixtures(sport_type: SportType, target_date: date) -> list[Fixture]:
    return await _FETCHERS[sport_type](target_date)
