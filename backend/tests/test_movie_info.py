"""Movie categories for players (1.0.24)."""

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.config import get_settings
from app.main import app
from app.models.base import ChannelType, SourceType
from app.models.movie_info import MovieInfo, TmdbTrending
from app.models.playlist import Playlist, PlaylistCategory, PlaylistChannel
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.xc_user import XcUser, XcUserPlaylist
from app.services import movie_info
from app.services.xtream_client import ChannelData

NOW = datetime.now(timezone.utc)
RECENT = (date.today() - timedelta(days=20)).isoformat()


def test_provider_release_dates():
    assert movie_info.provider_release("2023-05-12") == "2023-05-12"
    assert movie_info.provider_release("2023-05-12 00:00:00") == "2023-05-12"
    assert movie_info.provider_release("12/05/2023") == "2023-05-12"
    assert movie_info.provider_release("2023") == "2023"
    assert movie_info.provider_release("2023-02-30") == "2023"
    assert movie_info.provider_release("soon") is None
    assert movie_info.provider_release(None) is None


def test_titles_are_cleaned_for_the_search():
    assert movie_info.clean_title("4K-AMZ - Elf Me (2023)") == "Elf Me"
    assert movie_info.clean_title("4K-NF - Gordon (2026) (AR)") == "Gordon"
    assert movie_info.clean_title("|EN| Madden (2026)") == "Madden"
    assert movie_info.clean_title("Ali G: Who Iz I? (2026)") == "Ali G: Who Iz I?"
    assert movie_info.title_year("4K-AMZ - Elf Me (2023)") == 2023
    assert movie_info.title_year("Heat - 1995") == 1995
    assert movie_info.title_year("Heat") is None


def test_the_providers_film_fields():
    def data(**extra):
        return ChannelData(category_external_id="1", external_stream_id="1", name="x", stream_type=ChannelType.VOD, extra=extra)

    assert data(releaseDate="2024-03-01", tmdb="603", rating="7.9").film == ("2024-03-01", "603", 7.9)
    assert data(release_date="2024", tmdb_id=0, rating="").film == ("2024", None, None)
    assert data().film == (None, None, None)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded(db):
    src = Source(name="Panel", type=SourceType.XTREAM, base_url="http://panel.example", username="u", password="p")
    db.add(src)
    await db.flush()
    scat = SourceCategory(source_id=src.id, external_id="1", name="Films", channel_type=ChannelType.VOD)
    db.add(scat)
    await db.flush()

    def film(name, ext, **kw):
        return SourceChannel(source_category_id=scat.id, external_stream_id=ext, name=name, stream_type=ChannelType.VOD,
                             first_seen_at=NOW, last_seen_at=NOW, **kw)

    new = film("4K-AMZ - Madden (2026)", "1", provider_release=RECENT)
    old = film("Heat (1995)", "2")
    adult = film("Late Night (2026)", "3", provider_release=RECENT)
    db.add_all([new, old, adult])
    await db.flush()
    db.add_all([
        MovieInfo(source_channel_id=new.id, tmdb_id=10, release_date=RECENT, rating=6.7, popularity=50,
                  genres=json.dumps(["Drama", "Comedy"]), tmdb_checked=True, looked_up_at=NOW),
        MovieInfo(source_channel_id=old.id, tmdb_id=20, release_date="1995-12-15", rating=8.3, popularity=30,
                  genres=json.dumps(["Crime", "Drama"]), tmdb_checked=True, looked_up_at=NOW),
        TmdbTrending(tmdb_id=10, rank=0, fetched_at=NOW),
    ])
    pl = Playlist(name="Main")
    db.add(pl)
    await db.flush()
    films_cat = PlaylistCategory(playlist_id=pl.id, name="Amazon Movies", channel_type=ChannelType.VOD, sort_order=0)
    adults_cat = PlaylistCategory(playlist_id=pl.id, name="FOR ADULTS", channel_type=ChannelType.VOD, sort_order=1)
    db.add_all([films_cat, adults_cat])
    await db.flush()
    pc_new = PlaylistChannel(playlist_category_id=films_cat.id, source_channel_id=new.id, name="Madden")
    pc_old = PlaylistChannel(playlist_category_id=films_cat.id, source_channel_id=old.id, name="Heat")
    pc_adult = PlaylistChannel(playlist_category_id=adults_cat.id, source_channel_id=adult.id, name="Late Night")
    db.add_all([pc_new, pc_old, pc_adult])
    user = XcUser(username="damian", password="pw")
    db.add(user)
    await db.flush()
    db.add(XcUserPlaylist(xc_user_id=user.id, playlist_id=pl.id))
    await db.commit()
    return {"new": pc_new.id, "old": pc_old.id, "adult": pc_adult.id, "src_new": new.id, "src_old": old.id}


async def test_players_get_the_categories(client, seeded):
    r = await client.get("/player_api.php", params={"username": "damian", "password": "pw", "action": "dptv_smart_categories"})
    assert r.status_code == 200
    body = r.json()
    cats = {c["id"]: c for c in body["categories"]}
    assert [c["id"] for c in body["categories"]] == [
        "trending", "new", "genre:comedy", "genre:crime", "genre:drama", "decade:2020", "decade:1990",
    ]
    assert cats["trending"]["streams"] == [seeded["new"]]
    assert cats["new"]["streams"] == [seeded["new"]]
    assert cats["genre:drama"]["streams"] == [seeded["new"], seeded["old"]]  # most popular first
    assert cats["decade:1990"]["name"] == "1990s"
    assert body["films"][str(seeded["old"])] == ["1995-12-15", 8.3]
    assert str(seeded["adult"]) not in body["films"]  # "For Adults" is left out


async def test_run_without_a_key_keeps_the_providers_details(db, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "tmdb_api_key", "")
    await db.execute(MovieInfo.__table__.delete())
    await db.commit()
    result = await movie_info.run_movie_info(db)
    assert result["looked_up"] == 3
    info = await db.get(MovieInfo, seeded["src_new"])
    assert info.release_date == RECENT and info.tmdb_checked is False
    old = await db.get(MovieInfo, seeded["src_old"])
    assert old.release_date == "1995"  # the title's year


async def test_run_with_a_key_fills_in_tmdb(db, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "tmdb_api_key", "k")
    await db.execute(MovieInfo.__table__.delete())
    await db.commit()

    async def fake_lookup(client, key, sc):
        return None if "Late" in sc.name else {
            "id": 99, "release_date": "1995-12-15", "vote_average": 8.0, "vote_count": 100, "popularity": 9.0, "genres": ["Crime"],
        }

    async def fake_trending(db, client, key):
        return 0

    monkeypatch.setattr(movie_info, "_lookup", fake_lookup)
    monkeypatch.setattr(movie_info, "refresh_trending", fake_trending)
    result = await movie_info.run_movie_info(db)
    assert result == {"looked_up": 3, "found": 2, "trending": 0}
    new = await db.get(MovieInfo, seeded["src_new"])
    assert new.release_date == RECENT  # the provider's date wins over TMDB's
    assert json.loads(new.genres) == ["Crime"] and new.rating == 8.0
    # Nothing left to look up on a second run.
    assert (await movie_info.run_movie_info(db))["looked_up"] == 0
