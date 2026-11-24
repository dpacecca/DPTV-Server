"""Provider catch-up passed through to players (1.0.23)."""

from datetime import datetime, timezone

import httpx
import pytest

from app.main import app
from app.models.base import ChannelType, SourceType
from app.models.playlist import Playlist, PlaylistCategory, PlaylistChannel
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.xc_user import XcUser, XcUserPlaylist
from app.services import catchup
from app.services.xtream_client import ChannelData

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def zones(monkeypatch):
    catchup.forget_zones()
    asked = []

    async def fake_zone(source):
        asked.append(source.username)
        return "Australia/Sydney"

    monkeypatch.setattr(catchup, "provider_zone", fake_zone)
    return asked


@pytest.fixture
async def seeded(db):
    panel = Source(name="Panel", type=SourceType.XTREAM, base_url="http://panel.example:8080", username="server", password="srv")
    line = Source(name="Own line", type=SourceType.XTREAM, base_url="http://panel.example:8080", username="own", password="op")
    db.add_all([panel, line])
    await db.flush()
    cat = SourceCategory(source_id=panel.id, external_id="1", name="Sport", channel_type=ChannelType.LIVE)
    db.add(cat)
    await db.flush()
    kept = SourceChannel(
        source_category_id=cat.id, external_stream_id="501", name="Kept", stream_type=ChannelType.LIVE,
        stream_url="http://panel.example:8080/live/server/srv/501.ts", tv_archive=True, tv_archive_duration=7,
        first_seen_at=NOW, last_seen_at=NOW,
    )
    plain = SourceChannel(
        source_category_id=cat.id, external_stream_id="502", name="Plain", stream_type=ChannelType.LIVE,
        stream_url="http://panel.example:8080/live/server/srv/502.ts", first_seen_at=NOW, last_seen_at=NOW,
    )
    db.add_all([kept, plain])
    await db.flush()
    pl = Playlist(name="Main")
    db.add(pl)
    await db.flush()
    pcat = PlaylistCategory(playlist_id=pl.id, name="Live", channel_type=ChannelType.LIVE, sort_order=0)
    db.add(pcat)
    await db.flush()
    pc_kept = PlaylistChannel(playlist_category_id=pcat.id, source_channel_id=kept.id, name="Kept")
    pc_plain = PlaylistChannel(playlist_category_id=pcat.id, source_channel_id=plain.id, name="Plain")
    db.add_all([pc_kept, pc_plain])
    shared = XcUser(username="damian", password="pw-d")
    own = XcUser(username="emidio", password="pw-e", source_override_id=line.id)
    db.add_all([shared, own])
    await db.flush()
    db.add_all([XcUserPlaylist(xc_user_id=shared.id, playlist_id=pl.id), XcUserPlaylist(xc_user_id=own.id, playlist_id=pl.id)])
    await db.commit()
    return {"kept": pc_kept.id, "plain": pc_plain.id}


def test_the_archive_is_read_from_the_providers_fields():
    def data(**extra):
        return ChannelData(category_external_id="1", external_stream_id="1", name="x", stream_type=ChannelType.LIVE, extra=extra)

    assert data(tv_archive=1, tv_archive_duration="7").archive == (True, 7)
    assert data(tv_archive="0", tv_archive_duration=7).archive == (False, 0)
    assert data(tv_archive="yes").archive == (False, 0)
    assert data().archive == (False, 0)


async def test_live_streams_carry_the_channels_catch_up(client, seeded):
    r = await client.get("/player_api.php", params={"username": "damian", "password": "pw-d", "action": "get_live_streams"})
    by_id = {s["stream_id"]: s for s in r.json()}
    assert (by_id[seeded["kept"]]["tv_archive"], by_id[seeded["kept"]]["tv_archive_duration"]) == (1, 7)
    assert (by_id[seeded["plain"]]["tv_archive"], by_id[seeded["plain"]]["tv_archive_duration"]) == (0, 0)


async def test_timeshift_goes_to_the_panel_in_its_own_time_zone(client, seeded):
    # 09:30 UTC is 20:30 in Sydney (UTC+11 in November).
    r = await client.get(f"/timeshift/damian/pw-d/60/2026-11-24:09-30/{seeded['kept']}.ts")
    assert r.status_code == 302
    assert r.headers["location"] == "http://panel.example:8080/timeshift/server/srv/60/2026-11-24:20-30/501.ts"


async def test_timeshift_uses_the_logins_own_line(client, seeded, zones):
    r = await client.get(f"/timeshift/emidio/pw-e/30/2026-11-24:09-30/{seeded['kept']}.m3u8")
    assert r.headers["location"] == "http://panel.example:8080/timeshift/own/op/30/2026-11-24:20-30/501.m3u8"
    assert zones == ["own"]


async def test_timeshift_php_form(client, seeded):
    r = await client.get("/streaming/timeshift.php", params={
        "username": "damian", "password": "pw-d", "stream": seeded["kept"], "start": "2026-11-24:09-30", "duration": "45",
    })
    assert r.headers["location"] == "http://panel.example:8080/timeshift/server/srv/45/2026-11-24:20-30/501.ts"


async def test_bad_requests_are_refused(client, seeded):
    assert (await client.get(f"/timeshift/damian/wrong/60/2026-11-24:09-30/{seeded['kept']}.ts")).status_code == 401
    assert (await client.get(f"/timeshift/damian/pw-d/60/yesterday/{seeded['kept']}.ts")).status_code == 400
    assert (await client.get(f"/timeshift/damian/pw-d/0/2026-11-24:09-30/{seeded['kept']}.ts")).status_code == 400
    assert (await client.get("/timeshift/damian/pw-d/60/2026-11-24:09-30/99999.ts")).status_code == 404


def test_start_parsing_and_zones():
    assert catchup.parse_start("2026-11-24:09-30") == datetime(2026, 11, 24, 9, 30, tzinfo=timezone.utc)
    assert catchup.parse_start("2026-11-24:09:30") == datetime(2026, 11, 24, 9, 30, tzinfo=timezone.utc)
    assert catchup.parse_start("junk") is None
    start = datetime(2026, 11, 24, 9, 30, tzinfo=timezone.utc)
    assert catchup.timeshift_url("http://p/", "u", "w", 60, start, "5", "ts", None) == "http://p/timeshift/u/w/60/2026-11-24:09-30/5.ts"
    assert catchup.timeshift_url("http://p", "u", "w", 60, start, "5", "ts", "Not/AZone").endswith("/2026-11-24:09-30/5.ts")
