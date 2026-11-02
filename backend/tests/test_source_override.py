"""A per-login panel line (1.0.10): an XC login's source override sends its streams to the panel
with that login's own credentials, for channels from that panel only."""

from datetime import datetime, timezone

import httpx
import pytest

from app.api.routes.xc_server import same_panel
from app.main import app
from app.models.base import ChannelType, SourceType
from app.models.playlist import Playlist, PlaylistCategory, PlaylistChannel
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.xc_user import XcUser, XcUserPlaylist

SHARED = {"username": "damian", "password": "pw-d"}
OWN = {"username": "emidio", "password": "pw-e"}
NOW = datetime.now(timezone.utc)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded(db):
    panel = Source(name="Panel", type=SourceType.XTREAM, base_url="http://panel.example:8080", username="server", password="srv")
    other = Source(name="Other", type=SourceType.XTREAM, base_url="http://other.example", username="o", password="op")
    line = Source(name="Emidio line", type=SourceType.XTREAM, base_url="HTTP://Panel.example:8080/", username="emidio-line", password="el")
    db.add_all([panel, other, line])
    await db.flush()
    cat = SourceCategory(source_id=panel.id, external_id="1", name="Sport", channel_type=ChannelType.LIVE)
    other_cat = SourceCategory(source_id=other.id, external_id="1", name="News", channel_type=ChannelType.LIVE)
    db.add_all([cat, other_cat])
    await db.flush()
    sky = SourceChannel(
        source_category_id=cat.id, external_stream_id="337150", name="Sky Sport 2", stream_type=ChannelType.LIVE,
        stream_url="http://panel.example:8080/live/server/srv/337150.ts", first_seen_at=NOW, last_seen_at=NOW,
    )
    news = SourceChannel(
        source_category_id=other_cat.id, external_stream_id="9", name="News", stream_type=ChannelType.LIVE,
        stream_url="http://other.example/live/o/op/9.ts", first_seen_at=NOW, last_seen_at=NOW,
    )
    db.add_all([sky, news])
    await db.flush()
    pl = Playlist(name="Main")
    db.add(pl)
    await db.flush()
    pcat = PlaylistCategory(playlist_id=pl.id, name="Live", channel_type=ChannelType.LIVE, sort_order=0)
    db.add(pcat)
    await db.flush()
    pc_sky = PlaylistChannel(playlist_category_id=pcat.id, source_channel_id=sky.id, name="Sky Sport 2")
    pc_news = PlaylistChannel(playlist_category_id=pcat.id, source_channel_id=news.id, name="News")
    db.add_all([pc_sky, pc_news])
    shared = XcUser(username="damian", password="pw-d")
    own = XcUser(username="emidio", password="pw-e", source_override_id=line.id)
    db.add_all([shared, own])
    await db.flush()
    db.add_all([XcUserPlaylist(xc_user_id=shared.id, playlist_id=pl.id), XcUserPlaylist(xc_user_id=own.id, playlist_id=pl.id)])
    await db.commit()
    return {"sky": pc_sky.id, "news": pc_news.id}


async def _target(client, creds, channel_id, ext="ts"):
    r = await client.get(f"/live/{creds['username']}/{creds['password']}/{channel_id}.{ext}")
    assert r.status_code == 302
    return r.headers["location"]


async def test_without_an_override_the_shared_line_is_used(client, seeded):
    assert await _target(client, SHARED, seeded["sky"]) == "http://panel.example:8080/live/server/srv/337150.ts"


async def test_the_override_sends_this_login_to_its_own_line(client, seeded):
    # The line's address as typed ("HTTP://Panel.example:8080/"), with its own login.
    target = await _target(client, OWN, seeded["sky"], "m3u8")
    assert target.lower() == "http://panel.example:8080/live/emidio-line/el/337150.m3u8"


async def test_a_channel_from_another_panel_keeps_its_own_source(client, seeded):
    assert await _target(client, OWN, seeded["news"]) == "http://other.example/live/o/op/9.ts"


def test_same_panel_compares_scheme_host_and_port():
    assert same_panel("http://Panel.example:8080/", "http://panel.example:8080")
    assert same_panel("http://panel.example", "http://panel.example:80")
    assert not same_panel("http://panel.example:8080", "http://panel.example:8081")
    assert not same_panel("http://panel.example", "http://other.example")
    assert not same_panel(None, "http://panel.example")
