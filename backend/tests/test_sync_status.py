from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.main import app
from app.models.base import ChannelType, SourceType
from app.models.playlist import Playlist, PlaylistCategory, PlaylistChannel
from app.models.source import Source, SourceChannel
from app.models.xc_user import XcUser, XcUserPlaylist
from app.services import sync_engine
from app.services.sync_status import compute_sync_status
from app.services.xtream_client import CategoryData, ChannelData

ALICE = {"username": "alice", "password": "pw-a"}


def _provider_data(stream_url_suffix: str = "", logo: str = "http://logo/1.png"):
    cats = [CategoryData(external_id="10", name="News", channel_type=ChannelType.LIVE, sort_order=0)]
    chans = [
        ChannelData(
            category_external_id="10",
            external_stream_id="1",
            name="Channel One",
            stream_type=ChannelType.LIVE,
            sort_order=0,
            logo_url=logo,
            stream_url=f"http://provider/1.ts{stream_url_suffix}",
        ),
        ChannelData(
            category_external_id="10",
            external_stream_id="2",
            name="Channel Two",
            stream_type=ChannelType.LIVE,
            sort_order=1,
            stream_url="http://provider/2.ts",
        ),
    ]
    return cats, chans


@pytest.fixture
def provider(monkeypatch):
    """Lets a test swap what the (fake) M3U provider returns on the next sync."""
    state = {"data": _provider_data()}

    async def fake_fetch(_source):
        return state["data"]

    monkeypatch.setattr(sync_engine, "fetch_m3u_categories_and_channels", fake_fetch)
    return state


@pytest.fixture
async def seeded(db, provider):
    source = Source(name="Prov", type=SourceType.M3U, m3u_url="http://provider/list.m3u")
    db.add(source)
    await db.flush()
    await sync_engine.sync_source(db, source)

    pl = Playlist(name="Main")
    other_pl = Playlist(name="Bob's")
    db.add_all([pl, other_pl])
    await db.flush()
    live = PlaylistCategory(playlist_id=pl.id, name="Live", channel_type=ChannelType.LIVE, sort_order=0)
    vod = PlaylistCategory(playlist_id=pl.id, name="Movies", channel_type=ChannelType.VOD, sort_order=1)
    bob_live = PlaylistCategory(playlist_id=other_pl.id, name="Bob live", channel_type=ChannelType.LIVE)
    db.add_all([live, vod, bob_live])
    await db.flush()

    scs = (await db.execute(select(SourceChannel).order_by(SourceChannel.external_stream_id))).scalars().all()
    for i, sc in enumerate(scs):
        db.add(PlaylistChannel(playlist_category_id=live.id, source_channel_id=sc.id, name=sc.name, sort_order=i))
    db.add(PlaylistChannel(playlist_category_id=vod.id, name="A Movie", manual_stream_url="http://x/m.mp4"))
    db.add(PlaylistChannel(playlist_category_id=bob_live.id, name="Bob One", manual_stream_url="http://x/b.ts"))

    alice = XcUser(username="alice", password="pw-a")
    bob = XcUser(username="bob", password="pw-b")
    db.add_all([alice, bob])
    await db.flush()
    db.add_all(
        [
            XcUserPlaylist(xc_user_id=alice.id, playlist_id=pl.id),
            XcUserPlaylist(xc_user_id=bob.id, playlist_id=other_pl.id),
        ]
    )
    await db.commit()
    return {"source": source.id, "alice": alice.id, "bob": bob.id}


async def _revs(db, user_id: int) -> dict[str, str]:
    db.expire_all()
    user = await db.get(XcUser, user_id)
    status = await compute_sync_status(db, user)
    return {k: v.rev for k, v in status.revs.items()}


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- revision semantics --------------------------------------------------------------------------


async def test_rev_changes_on_channel_edit(db, seeded):
    alice = seeded["alice"]
    before = await _revs(db, alice)
    pc = (await db.execute(select(PlaylistChannel).where(PlaylistChannel.name == "Channel One"))).scalar_one()
    pc.name = "Channel One HD"
    await db.commit()
    after = await _revs(db, alice)
    assert after["live"] != before["live"]
    assert after["epg"] != before["epg"]  # the guide shows channel names too
    assert after["vod"] == before["vod"]
    assert after["series"] == before["series"]


async def test_rev_changes_on_channel_delete(db, seeded):
    alice = seeded["alice"]
    before = await _revs(db, alice)
    pc = (await db.execute(select(PlaylistChannel).where(PlaylistChannel.name == "A Movie"))).scalar_one()
    await db.delete(pc)
    await db.commit()
    after = await _revs(db, alice)
    assert after["vod"] != before["vod"]
    assert after["live"] == before["live"]


async def test_rev_changes_when_playlist_unassigned(db, seeded):
    alice = seeded["alice"]
    before = await _revs(db, alice)
    link = (await db.execute(select(XcUserPlaylist).where(XcUserPlaylist.xc_user_id == alice))).scalar_one()
    link.enabled = False
    await db.commit()
    assert (await _revs(db, alice))["live"] != before["live"]


async def test_source_sync_without_changes_keeps_rev(db, seeded):
    alice = seeded["alice"]
    before = await _revs(db, alice)
    source = await db.get(Source, seeded["source"])
    await sync_engine.sync_source(db, source)
    await db.commit()
    assert await _revs(db, alice) == before
    # ...but the bookkeeping still happened.
    db.expire_all()
    source = await db.get(Source, seeded["source"])
    sc = (await db.execute(select(SourceChannel).where(SourceChannel.external_stream_id == "1"))).scalar_one()
    assert sc.last_seen_at == source.last_sync_at


async def test_source_sync_with_new_stream_url_changes_rev(db, seeded, provider):
    alice, bob = seeded["alice"], seeded["bob"]
    before = await _revs(db, alice)
    bob_before = await _revs(db, bob)
    provider["data"] = _provider_data(stream_url_suffix="?v=2")
    source = await db.get(Source, seeded["source"])
    await sync_engine.sync_source(db, source)
    await db.commit()
    after = await _revs(db, alice)
    assert after["live"] != before["live"]
    assert after["vod"] == before["vod"]
    # Bob's playlist doesn't use this provider: his revisions don't move.
    assert await _revs(db, bob) == bob_before


async def test_source_sync_logo_change_changes_rev(db, seeded, provider):
    alice = seeded["alice"]
    before = await _revs(db, alice)
    provider["data"] = _provider_data(logo="http://logo/new.png")
    source = await db.get(Source, seeded["source"])
    await sync_engine.sync_source(db, source)
    await db.commit()
    assert (await _revs(db, alice))["live"] != before["live"]


# --- HTTP ----------------------------------------------------------------------------------------


async def test_sync_status_action(client, seeded):
    r = await client.get("/player_api.php", params={**ALICE, "action": "dptv_sync_status"})
    assert r.status_code == 200
    body = r.json()
    assert body["server"] == "DPTV-Server"
    assert set(body) == {
        "server", "version", "live", "vod", "series", "epg", "last_source_sync_at", "last_epg_refresh_at",
    }
    for kind in ("live", "vod", "series", "epg"):
        assert isinstance(body[kind]["rev"], str) and len(body[kind]["rev"]) == 16
    assert isinstance(body["live"]["updated_at"], int)
    assert isinstance(body["last_source_sync_at"], int)
    assert body["last_epg_refresh_at"] is None
    assert r.headers["cache-control"] == "no-cache"


async def test_sync_status_requires_auth(client, seeded):
    r = await client.get("/player_api.php", params={"username": "alice", "password": "nope", "action": "dptv_sync_status"})
    assert r.status_code == 401


async def test_list_action_etag_and_304(client, db, seeded):
    params = {**ALICE, "action": "get_live_streams"}
    r = await client.get("/player_api.php", params=params)
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert r.headers["cache-control"] == "no-cache"
    assert "last-modified" in r.headers
    assert [s["name"] for s in r.json()] == ["Channel One", "Channel Two"]
    status = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_sync_status"})).json()
    assert etag == f'"{status["live"]["rev"]}"'

    r304 = await client.get("/player_api.php", params=params, headers={"If-None-Match": etag})
    assert r304.status_code == 304
    assert r304.content == b""
    assert r304.headers["etag"] == etag

    # Weak form (as handed back by e.g. nginx with gzip) also matches.
    assert (await client.get("/player_api.php", params=params, headers={"If-None-Match": f"W/{etag}"})).status_code == 304

    r_ims = await client.get("/player_api.php", params=params, headers={"If-Modified-Since": r.headers["last-modified"]})
    assert r_ims.status_code == 304

    # After a deletion the old validators no longer match (Last-Modified moves too).
    pc = (await db.execute(select(PlaylistChannel).where(PlaylistChannel.name == "Channel Two"))).scalar_one()
    await db.delete(pc)
    await db.commit()
    r2 = await client.get("/player_api.php", params=params, headers={"If-None-Match": etag})
    assert r2.status_code == 200
    assert [s["name"] for s in r2.json()] == ["Channel One"]
    r3 = await client.get("/player_api.php", params=params, headers={"If-Modified-Since": r.headers["last-modified"]})
    assert r3.status_code == 200


async def test_get_php_head_and_conditional(client, seeded):
    r = await client.get("/get.php", params=ALICE)
    assert r.status_code == 200
    assert r.text.startswith("#EXTM3U\n")
    assert "Channel One" in r.text and "Bob One" not in r.text
    etag = r.headers["etag"]

    h = await client.head("/get.php", params=ALICE)
    assert h.status_code == 200
    assert h.content == b""
    assert h.headers["etag"] == etag
    assert h.headers["last-modified"] == r.headers["last-modified"]
    assert h.headers["cache-control"] == "no-cache"
    assert h.headers["content-type"] == r.headers["content-type"]

    assert (await client.get("/get.php", params=ALICE, headers={"If-None-Match": etag})).status_code == 304
    assert (await client.head("/get.php", params=ALICE, headers={"If-None-Match": etag})).status_code == 304
    assert (await client.get("/get.php", params=ALICE, headers={"If-None-Match": '"other"'})).status_code == 200


async def test_xmltv_head_and_conditional(client, seeded):
    r = await client.get("/xmltv.php", params=ALICE)
    assert r.status_code == 200
    assert r.text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="DPTV-Server">')
    etag = r.headers["etag"]
    status = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_sync_status"})).json()
    assert etag == f'"{status["epg"]["rev"]}"'

    h = await client.head("/xmltv.php", params=ALICE)
    assert h.status_code == 200 and h.content == b"" and h.headers["etag"] == etag
    assert (await client.get("/xmltv.php", params=ALICE, headers={"If-None-Match": etag})).status_code == 304


async def test_bare_player_api_unchanged(client, seeded):
    r = await client.get("/player_api.php", params=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert body["user_info"]["username"] == "alice"
    assert "etag" not in r.headers


async def test_unsupported_action_still_400(client, seeded):
    r = await client.get("/player_api.php", params={**ALICE, "action": "bogus"})
    assert r.status_code == 400


def test_last_modified_strictly_increases_on_change():
    from app.services.sync_status import Rev, last_modified_for

    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = last_modified_for(999, "live", Rev(rev="a", updated_at=t))
    assert last_modified_for(999, "live", Rev(rev="a", updated_at=t)) == first
    second = last_modified_for(999, "live", Rev(rev="b", updated_at=t))
    assert second > first
