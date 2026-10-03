import logging
from datetime import date, datetime, timezone

from app.services import allsports_client
from app.services.sport_data import Fixture

logger = logging.getLogger("dptv.allsports_rugby")

# AllSportsApi's own Rugby Union category - covers every rugby union competition it tracks, not
# just the ones below. There's no server-side filter for "only these tournaments", so
# fetch_rugby_matches() pulls everything for the day and filters client-side.
_RUGBY_UNION_CATEGORY_ID = 82

# The admin's own curated list of competitions worth a daily digest, keyed by AllSportsApi's
# stable uniqueTournament.id (confirmed against a real GET /api/rugby/tournament/all/category/82
# response - tournament.id instead would drift between playoffs/pools within a single
# competition, per that endpoint's own docs). Values are the display names used in the digest,
# which don't have to match the API's own (sometimes awkward - e.g. "Prem Rugby") names.
RUGBY_COMPETITIONS: dict[int, str] = {
    423: "Six Nations",
    34478: "Nations Championship",
    876: "International Friendlies / Tests",
    1056: "U20 Junior World Championship",
    26481: "U20 The Rugby Championship",
    1738: "Pacific Nations Cup",
    422: "Super Rugby Pacific",
    29227: "Super Rugby AUS",
    797: "NPC",
    420: "Top 14",
    1147: "Pro D2",
    424: "English Premiership",
    419: "United Rugby Championship",
    566: "Serie A Elite",
    796: "Currie Cup",
}


async def fetch_rugby_matches(target_date: date) -> list[Fixture]:
    """Every match on `target_date` in RUGBY_COMPETITIONS, across every supported rugby
    competition AllSportsApi tracks under Rugby Union. Reuses sport_data.Fixture (not a
    rugby_digest-specific type) purely for its shape - this never touches SportFixtureCache or
    the SportType-keyed Live Sport category pipeline, since it's a different provider and a
    deliberately different, admin-curated set of competitions from whatever a Live Sport Rugby
    category or sport-type Dummy EPG rule is configured to use."""
    data = await allsports_client.get_json(
        f"/api/rugby/category/{_RUGBY_UNION_CATEGORY_ID}/events/"
        f"{target_date.day}/{target_date.month}/{target_date.year}"
    )

    fixtures: list[Fixture] = []
    for row in data.get("events", []):
        try:
            tournament_id = row["tournament"]["uniqueTournament"]["id"]
            competition = RUGBY_COMPETITIONS.get(tournament_id)
            if competition is None:
                continue
            home, away = row["homeTeam"], row["awayTeam"]
            is_live, is_finished = allsports_client.classify_status(row.get("status", {}).get("type", ""))
            fixtures.append(
                Fixture(
                    id=str(row["id"]),
                    competition=competition,
                    kickoff=datetime.fromtimestamp(row["startTimestamp"], tz=timezone.utc),
                    # shortName ("Toulouse", "Leinster"), not the full "Stade Toulousain"/
                    # "Leinster Rugby" form - a provider channel name is far more likely to use
                    # the common short form (same shortDisplayName-vs-displayName reasoning as
                    # the NFL fetcher in allsports_nfl.py). Falls back to the full name on the
                    # rare row missing shortName.
                    home=home.get("shortName") or home["name"],
                    away=away.get("shortName") or away["name"],
                    is_live=is_live,
                    is_finished=is_finished,
                    home_score=(row.get("homeScore") or {}).get("current"),
                    away_score=(row.get("awayScore") or {}).get("current"),
                    home_display=home["name"],
                    away_display=away["name"],
                )
            )
        except (KeyError, TypeError):
            logger.warning("Skipping malformed AllSportsApi rugby event row: %r", row)
    return fixtures
