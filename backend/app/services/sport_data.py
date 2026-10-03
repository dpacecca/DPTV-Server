import logging
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.base import SportType

logger = logging.getLogger("dptv.sport_data")

# Display name shown in the "Create Live Sport Category" picker and used to name the category
# itself (e.g. "Live Rugby") - the single source of truth for which sports are supported.
SPORT_LABELS: dict[SportType, str] = {
    SportType.RUGBY: "Live Rugby",
    SportType.NFL: "Live NFL",
}

# A sport-appropriate default event duration (minutes), used to seed a new Live Sport category's
# dummy_epg_program_minutes at creation time - the generic 60-minute column default badly
# undersells most sports (an NFL broadcast window runs 3+ hours), which would cut the generated
# event tile short and fall into "finished" filler while the game is still actually on. Still
# just the normal, admin-editable Program length field afterward (Category Settings) - this only
# picks a sane starting point instead of a one-size-fits-all default.
SPORT_EVENT_MINUTES: dict[SportType, int] = {
    SportType.RUGBY: 100,
    SportType.NFL: 210,
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
    # `home`/`away` above are whatever shape the matcher needs (see _matching_channels_for_fixture
    # in sport_refresh.py) - for a provider that distinguishes a short/mascot form ("Bills") from
    # a full "City Mascot" form ("Buffalo Bills"), that's deliberately the *short* form, since
    # that's what tends to actually appear inside a real provider channel name. The generated
    # event title (see sport_refresh._format_event_title) wants the fuller, more readable form
    # instead, hence these separate optional fields - falling back to `home`/`away` themselves
    # for a provider (like rugby) that doesn't distinguish the two at all.
    home_display: str | None = None
    away_display: str | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    venue_state: str | None = None
    home_id: str | None = None
    """The provider's own id for the home team, if it has one - currently only set by
    allsports_nfl.py, which needs it to look up (and lazily cache) that team's home stadium (see
    allsports_nfl.attach_venues) since AllSportsApi's fixtures feed carries no per-game venue."""


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


async def fetch_fixtures(db: AsyncSession, sport_type: SportType, target_date: date) -> list[Fixture]:
    """NFL is special-cased rather than living in _FETCHERS like every other sport: unlike a pure
    fetch, it additionally needs `db` to backfill each game's venue from a per-team cache (see
    allsports_nfl.attach_venues' docstring for why AllSportsApi can't just hand that over
    directly)."""
    if sport_type == SportType.NFL:
        from app.services import allsports_nfl  # local import - allsports_nfl imports Fixture from here

        fixtures = await allsports_nfl.fetch_nfl_fixtures(target_date)
        return await allsports_nfl.attach_venues(db, fixtures)
    return await _FETCHERS[sport_type](target_date)
