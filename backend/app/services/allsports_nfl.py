import logging
from dataclasses import replace
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.playlist import NflTeamVenue
from app.services import allsports_client
from app.services.sport_data import Fixture

logger = logging.getLogger("dptv.allsports_nfl")

# AllSportsApi's American Football category for the USA - covers college football too, so
# fetch_nfl_fixtures() filters down to just _NFL_TOURNAMENT_ID.
_AMERICAN_FOOTBALL_USA_CATEGORY_ID = 1370
# uniqueTournament.id for the NFL regular season - NFL Preseason is a separate id (9465),
# deliberately excluded (the admin confirmed this live against the real API before we wired it
# in, same as the rugby competition list).
_NFL_TOURNAMENT_ID = 9464


async def fetch_nfl_fixtures(target_date: date) -> list[Fixture]:
    """Every NFL game on `target_date`. No venue here - see attach_venues() below; like rugby,
    AllSportsApi's fixtures feed carries no per-game venue at all."""
    data = await allsports_client.get_json(
        f"/api/american-football/category/{_AMERICAN_FOOTBALL_USA_CATEGORY_ID}/events/"
        f"{target_date.day}/{target_date.month}/{target_date.year}"
    )

    fixtures: list[Fixture] = []
    for row in data.get("events", []):
        try:
            if row["tournament"]["uniqueTournament"]["id"] != _NFL_TOURNAMENT_ID:
                continue
            home, away = row["homeTeam"], row["awayTeam"]
            is_live, is_finished = allsports_client.classify_status(row.get("status", {}).get("type", ""))
            fixtures.append(
                Fixture(
                    id=str(row["id"]),
                    competition="NFL",
                    kickoff=datetime.fromtimestamp(row["startTimestamp"], tz=timezone.utc),
                    # shortName ("Commanders", "Colts"), not the full "Washington Commanders" -
                    # channel names overwhelmingly use the mascot name (e.g. "NFL | 02 - 1pm
                    # Chargers at Bills"), and the sport-fixture matcher requires this to appear
                    # as a literal substring. `homeTeam`/`awayTeam` are already correct here
                    # (confirmed against a real response) despite the tournament's own
                    # displayInverseHomeAwayTeams flag - that only tells AllSportsApi's own UI to
                    # render "Away @ Home" instead of "Home vs Away"; it doesn't mean the fields
                    # are swapped.
                    home=home.get("shortName") or home["name"],
                    away=away.get("shortName") or away["name"],
                    is_live=is_live,
                    is_finished=is_finished,
                    home_score=(row.get("homeScore") or {}).get("current"),
                    away_score=(row.get("awayScore") or {}).get("current"),
                    home_display=home["name"],
                    away_display=away["name"],
                    home_id=str(home["id"]),
                )
            )
        except (KeyError, TypeError):
            logger.warning("Skipping malformed AllSportsApi NFL event row: %r", row)
    return fixtures


def _split_state(state_code: str | None) -> str | None:
    """"US-LA" -> "LA" - AllSportsApi's venue.city.state is an ISO 3166-2 code, not the plain
    two-letter abbreviation the existing "Kick off HH:MM, Venue, City, ST" description expects
    (see dummy_epg._format_fixture_description)."""
    if not state_code:
        return None
    return state_code.rsplit("-", 1)[-1] or None


async def _fetch_team_venue(team_id: str) -> tuple[str | None, str | None, str | None]:
    """(venue_name, venue_city, venue_state) for one team's home stadium. The team endpoint's
    shape wasn't fully confirmed beyond the `venue` fragment itself, so this tolerates the team
    object either being the response's top level or nested under a "team" key."""
    data = await allsports_client.get_json(f"/api/american-football/team/{team_id}")
    team = data.get("team") if isinstance(data.get("team"), dict) else data
    venue = team.get("venue") or {}
    if not venue:
        return None, None, None
    city = (venue.get("city") or {}).get("name")
    state = _split_state((venue.get("city") or {}).get("state"))
    return venue.get("name"), city, state


async def _ensure_team_venues(db: AsyncSession, team_names: dict[str, str]) -> dict[str, NflTeamVenue]:
    """team_names is {team_id: team's full display name} for every distinct home team in this
    fetch - looks up each in NflTeamVenue, fetching (and persisting) only the ones not already
    cached. A team's venue is fetched once, ever, then reused for every future game it hosts."""
    team_ids = set(team_names)
    if not team_ids:
        return {}
    result = await db.execute(select(NflTeamVenue).where(NflTeamVenue.team_id.in_(team_ids)))
    cached = {row.team_id: row for row in result.scalars().all()}
    missing = team_ids - cached.keys()
    for team_id in missing:
        try:
            name, city, state = await _fetch_team_venue(team_id)
        except Exception:  # noqa: BLE001 - one team's venue failing must not break the whole fetch
            logger.exception("Failed to fetch venue for NFL team %s", team_id)
            continue
        if name is None:
            continue
        row = NflTeamVenue(team_id=team_id, team_name=team_names.get(team_id), venue_name=name, venue_city=city, venue_state=state)
        db.add(row)
        cached[team_id] = row
    if missing:
        await db.commit()
    return cached


async def attach_venues(db: AsyncSession, fixtures: list[Fixture]) -> list[Fixture]:
    """Fills in each fixture's venue from its home team's cached home stadium. AllSportsApi has
    no per-game venue at all, only a team's home ground, so this is necessarily wrong for a
    neutral-site or international game (e.g. a London/Germany game, or the kind of early-Sunday
    international slot a Colts @ Commanders game might be in) - there was no way to confirm or
    correct for that from the per-match endpoints, which came back empty on every attempt. Right
    for the overwhelming majority of games, and strictly better than no venue at all, which is
    the only alternative this provider offers."""
    team_names = {fx.home_id: (fx.home_display or fx.home) for fx in fixtures if fx.home_id}
    venues = await _ensure_team_venues(db, team_names)
    result: list[Fixture] = []
    for fx in fixtures:
        venue = venues.get(fx.home_id) if fx.home_id else None
        if venue is None:
            result.append(fx)
            continue
        result.append(
            replace(fx, venue_name=venue.venue_name, venue_city=venue.venue_city, venue_state=venue.venue_state)
        )
    return result
