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


_ESPN_HOST = "espn-api2.p.rapidapi.com"

# ESPN's own status.state convention (this RapidAPI wrapper passes it straight through):
# "pre" - not started, "in" - live, "post" - finished. Unlike rugby's free-text status field,
# this is a small closed set of known values, so exact membership is enough - no keyword-drift
# handling needed here.
_ESPN_LIVE_STATES = {"in"}
_ESPN_FINISHED_STATES = {"post"}


def _classify_espn_status(state: str) -> tuple[bool, bool]:
    """Returns (is_live, is_finished) - neither true means the fixture hasn't kicked off yet."""
    s = state.strip().lower()
    if s in _ESPN_LIVE_STATES:
        return True, False
    if s in _ESPN_FINISHED_STATES:
        return False, True
    return False, False


async def fetch_nfl_fixtures(target_date: date) -> list[Fixture]:
    """One day's NFL games. Unlike rugby's per-fixture endpoint, this scoreboard endpoint takes
    a single calendar date (YYYYMMDD, no separators) and returns every game kicking off that
    day - NFL games cluster on a handful of days a week (mostly Thu/Sun/Mon), so most other days
    in the lookahead window simply come back with an empty `events` list, which is expected, not
    an error."""
    settings = get_settings()
    if not settings.rapidapi_key:
        raise RuntimeError("DPTV_RAPIDAPI_KEY is not configured")

    url = f"https://{_ESPN_HOST}/api/v1/scoreboard"
    params = {"date": target_date.strftime("%Y%m%d"), "sport": "football", "league": "nfl", "limit": 100}
    headers = {"x-rapidapi-host": _ESPN_HOST, "x-rapidapi-key": settings.rapidapi_key}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    fixtures: list[Fixture] = []
    for row in data.get("events", []):
        try:
            kickoff = datetime.fromisoformat(row["date"])
            # Keyed by "homeAway" rather than assuming list order or parsing the free-text
            # `name`/`shortName` fields (which aren't even consistently formatted - e.g. one row
            # in a real response used "BAL VS DAL" where every other one used "LV @ NO").
            competitors = {c["homeAway"]: c for c in row["competitors"]}
            home, away = competitors["home"], competitors["away"]
            is_live, is_finished = _classify_espn_status(row.get("status", {}).get("state", ""))
            fixtures.append(
                Fixture(
                    id=str(row["id"]),
                    competition="NFL",
                    kickoff=kickoff,
                    # shortDisplayName ("Bills", "Chargers"), not the full "City Mascot" form
                    # ("Buffalo Bills") - channel names from providers overwhelmingly use just
                    # the mascot name (e.g. "NFL | 02 - 1pm Chargers at Bills"), and the matcher
                    # in sport_refresh.py requires the fixture's team name to appear as a literal
                    # substring, so matching on the full display name would silently match
                    # nothing on real-world channel names.
                    home=home["shortDisplayName"],
                    away=away["shortDisplayName"],
                    is_live=is_live,
                    is_finished=is_finished,
                    home_score=int(home["score"]) if home.get("score") not in (None, "") else None,
                    away_score=int(away["score"]) if away.get("score") not in (None, "") else None,
                    home_display=home.get("displayName") or home["shortDisplayName"],
                    away_display=away.get("displayName") or away["shortDisplayName"],
                    venue_name=(row.get("venue") or {}).get("name") or None,
                    venue_city=(row.get("venue") or {}).get("city") or None,
                    venue_state=(row.get("venue") or {}).get("state") or None,
                )
            )
        except (KeyError, ValueError):
            logger.warning("Skipping malformed NFL fixture row: %r", row)
    return fixtures


_FETCHERS = {SportType.RUGBY: fetch_rugby_fixtures, SportType.NFL: fetch_nfl_fixtures}


async def fetch_fixtures(sport_type: SportType, target_date: date) -> list[Fixture]:
    return await _FETCHERS[sport_type](target_date)
