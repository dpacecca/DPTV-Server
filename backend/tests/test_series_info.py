"""get_series_info keeps each episode's season (1.0.20)."""

from app.api.routes.xc_server import series_info_answer
from app.models.source import Source, SourceType
from app.services.xtream_client import XtreamClient, season_number


def _client() -> XtreamClient:
    return XtreamClient(Source(name="P", type=SourceType.XTREAM, base_url="http://p", username="u", password="pw"))


def test_episodes_stay_in_their_seasons_in_order():
    data = {
        "info": {"name": "Show", "plot": "A plot"},
        "seasons": [{"season_number": 1, "name": "Season 1"}, {"season_number": 2, "name": "Season 2"}],
        "episodes": {
            "2": [
                {"id": "21", "episode_num": "2", "title": "S2E2", "container_extension": "mkv", "season": 2},
                {"id": "20", "episode_num": 1, "title": "S2E1", "container_extension": "mkv", "info": {"plot": "p"}},
            ],
            "10": [{"id": "100", "episode_num": 1, "title": "S10E1", "container_extension": "mp4"}],
            "1": [{"id": "10", "episode_num": 1, "title": "S1E1", "container_extension": "mp4", "season": "1"}],
        },
    }
    client = _client()
    out = series_info_answer(data, client.series_episodes(data), 7)
    assert list(out["episodes"]) == ["1", "2", "10"]
    assert [e["title"] for e in out["episodes"]["2"]] == ["S2E1", "S2E2"], "by episode number"
    s2e1 = out["episodes"]["2"][0]
    assert s2e1["season"] == 2 and s2e1["id"] == "7_20" and s2e1["info"] == {"plot": "p"}
    assert s2e1["direct_source"].endswith("/series/passthrough/7_20.mkv")
    assert out["info"]["name"] == "Show" and len(out["seasons"]) == 2


def test_season_from_a_list_of_lists_and_fallbacks():
    data = {"episodes": [[{"id": "0", "episode_num": 1, "title": "Special"}], [{"id": "1", "episode_num": 1, "title": "Pilot"}]]}
    out = series_info_answer(data, _client().series_episodes(data), 3)
    assert list(out["episodes"]) == ["0", "1"], "the position is the season: 0 = specials"
    assert season_number(None, "4") == 4 and season_number("x", "y") == 1 and season_number(0, "3") == 0
    assert "info" not in series_info_answer({"episodes": {}}, [], 1)
