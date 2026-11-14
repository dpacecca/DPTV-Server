"""Versions of a channel from backup playlists (1.0.15)."""

import httpx
import pytest

from app.api.deps import require_admin
from app.main import app
from app.models.base import ChannelType
from app.models.playlist import ChannelVersion, Playlist, PlaylistCategory, PlaylistChannel, VersionSwap
from app.models.xc_user import XcUser, XcUserPlaylist
from app.services import channel_versions as cv
from app.services.stream_check import CheckResult, analyse

ALICE = {"username": "alice", "password": "pw-a"}


# ---------------------------------------------------------------- rules


def test_same_channel_by_name_country_and_guide():
    f = cv.facets
    assert cv.same_channel(f("UK: Sky Sports F1 FHD", None), f("Sky Sports F1 4K", None))
    assert not cv.same_channel(f("ES| DAZN 1", None), f("AR| DAZN 1", None))
    assert cv.same_channel(f("ES| DAZN 1", None), f("DAZN 1 HD", None))
    # One guide id: one name's words all in the other's.
    assert cv.same_channel(f("Sky Sports News", "skynews.uk"), f("Sky Sports News HQ", "skynews.uk"))
    assert not cv.same_channel(f("Sky Sports Main Event", "skynews.uk"), f("Sky Sports News", "skynews.uk"))
    # Too short to match by name alone.
    assert not cv.same_channel(f("TV", None), f("TV HD", None))


def test_analyse_finds_black_frozen_stalled_and_good():
    head = "Stream #0:0: Video: h264, yuv420p, 1920x1080, 25 fps\nStream #0:1: Audio: aac, 48000 Hz\n"
    good = analyse(head + "frame= 500 time=00:00:20.00 bitrate=N/A\n", 20)
    assert good.ok and good.height == 1080 and good.fps == 25 and good.silent is False
    black = analyse(head + "[blackdetect] black_start:0 black_end:18 black_duration:18\ntime=00:00:20.00\n", 20)
    assert not black.ok and "black" in black.reason
    frozen = analyse(head + "freeze_start: 2\ntime=00:00:20.00\n", 20)
    assert not frozen.ok and "frozen" in frozen.reason
    stalled = analyse(head + "time=00:00:05.00\n", 20)
    assert not stalled.ok and "stalled" in stalled.reason
    silent = analyse(head + "silence_start: 0\ntime=00:00:20.00\n", 20)
    assert silent.ok and silent.silent
    assert not analyse("Connection refused", 20).ok


def _row(cid: int, ok, height, streak, silent=False, reason="x") -> ChannelVersion:
    return ChannelVersion(main_channel_id=1, candidate_channel_id=cid, origin="auto", excluded=False,
                          ok=ok, height=height, pass_streak=streak, silent=silent, reason=reason)


def test_choose_needs_two_passes_and_a_better_picture():
    current = _row(1, True, 720, 3)
    assert cv.choose([current, _row(2, True, 1080, 1)], 1, False) is None, "one pass is not enough"
    assert cv.choose([current, _row(2, True, 1080, 2)], 1, False).to_id == 2
    assert cv.choose([current, _row(2, True, 720, 5)], 1, False) is None, "not a better picture"
    assert cv.choose([current, _row(2, True, 1080, 2)], 1, True) is None, "pinned"
    assert cv.choose([current, _row(2, False, 2160, 0, reason="black")], 1, False) is None, "a black 4K never wins"
    assert cv.choose([current, _row(2, True, 1080, 2, silent=True)], 1, False) is None, "silent only for a silent channel"
    failing = _row(1, False, 1080, 0, reason="frozen")
    assert cv.choose([failing, _row(2, True, 720, 2)], 1, False).to_id == 2, "a working version replaces a failing one"


# ---------------------------------------------------------------- end to end


@pytest.fixture
async def client():
    app.dependency_overrides[require_admin] = lambda: "admin"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(require_admin, None)


async def _playlist(db, name: str, channels: list[str]) -> tuple[Playlist, list[PlaylistChannel]]:
    pl = Playlist(name=name)
    db.add(pl)
    await db.flush()
    cat = PlaylistCategory(playlist_id=pl.id, name="Sport", channel_type=ChannelType.LIVE)
    db.add(cat)
    await db.flush()
    pcs = []
    for i, n in enumerate(channels):
        pc = PlaylistChannel(playlist_category_id=cat.id, name=n, manual_stream_url=f"http://provider/{name.replace(' ', '_')}/{i}.ts", sort_order=i)
        db.add(pc)
        pcs.append(pc)
    await db.flush()
    return pl, pcs


@pytest.fixture
async def setup(db):
    main, (f1, news) = await _playlist(db, "Main", ["UK: Sky Sports F1", "Sky News"])
    backup, (f1_4k, f1_es, other) = await _playlist(db, "Backup channels", ["Sky Sports F1 4K", "ES| Sky Sports F1", "Eurosport 1"])
    user = XcUser(username="alice", password="pw-a")
    db.add(user)
    await db.flush()
    db.add_all([XcUserPlaylist(xc_user_id=user.id, playlist_id=main.id), XcUserPlaylist(xc_user_id=user.id, playlist_id=backup.id)])
    await db.commit()
    return {"main": main.id, "backup": backup.id, "f1": f1.id, "f1_4k": f1_4k.id, "f1_es": f1_es.id, "news": news.id}


async def test_backup_playlist_matches_hides_and_plays_the_chosen_version(client, db, setup, monkeypatch):
    ids = setup
    r = await client.put(f"/api/playlists/{ids['backup']}", json={"name": "Backup channels", "backup_for_id": ids["main"]})
    assert r.status_code == 200 and r.json()["backup_for_id"] == ids["main"]

    detail = (await client.get(f"/api/versions/channels/{ids['f1']}")).json()
    assert {v["channel_id"] for v in detail["versions"]} == {ids["f1"], ids["f1_4k"]}, "the ES feed is another channel"
    summary = (await client.get(f"/api/versions/playlists/{ids['main']}/summary")).json()
    assert summary[str(ids["f1"])]["versions"] == 2 and str(ids["news"]) not in summary

    # Players don't see the backup playlist as one; they get the versions instead.
    streams = (await client.get("/player_api.php", params={**ALICE, "action": "get_live_streams"})).json()
    assert {s["name"] for s in streams} == {"UK: Sky Sports F1", "Sky News"}
    versions = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_versions", "stream_id": ids["f1"]})).json()
    assert [v["stream_id"] for v in versions][0] == ids["f1"] and len(versions) == 2

    # The check: the 4K version passes twice; the channel moves to it, and the stream follows.
    results = {f"http://provider/Main/0.ts": CheckResult(True, "720p 25 fps", 720, 25.0, None, False),
               f"http://provider/Backup_channels/0.ts": CheckResult(True, "2160p 50 fps", 2160, 50.0, None, False)}

    async def fake_check(url, sample_seconds, ffmpeg_path):
        return results[url]

    monkeypatch.setattr("app.services.stream_check.check_stream", fake_check)
    main_pc = await db.get(PlaylistChannel, ids["f1"])
    assert await cv.check_channel(db, main_pc, sample_seconds=1, ffmpeg_path="ffmpeg") == "kept"
    assert await cv.check_channel(db, main_pc, sample_seconds=1, ffmpeg_path="ffmpeg") == "swapped"
    await db.commit()
    db.expire_all()
    r = await client.get(f"/live/alice/pw-a/{ids['f1']}.ts", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "http://provider/Backup_channels/0.ts"
    detail = (await client.get(f"/api/versions/channels/{ids['f1']}")).json()
    assert detail["channel"]["active_id"] == ids["f1_4k"] and "passed 2 checks" in detail["swaps"][0]["reason"]

    # It goes black: the next checks move the channel back once its own feed has passed twice.
    results["http://provider/Backup_channels/0.ts"] = CheckResult(False, "black for 20 of 20 s", 2160, 50.0, None, False)
    main_pc = await db.get(PlaylistChannel, ids["f1"])
    assert await cv.check_channel(db, main_pc, sample_seconds=1, ffmpeg_path="ffmpeg") == "swapped"
    await db.commit()
    assert (await db.get(PlaylistChannel, ids["f1"])).active_version_id is None


async def test_pin_and_remove(client, db, setup):
    ids = setup
    await client.put(f"/api/playlists/{ids['backup']}", json={"name": "Backup channels", "backup_for_id": ids["main"]})
    assert (await client.put(f"/api/versions/channels/{ids['f1']}/pin", json={"candidate_channel_id": ids["f1_4k"]})).status_code == 200
    db.expire_all()
    pc = await db.get(PlaylistChannel, ids["f1"])
    assert pc.active_version_id == ids["f1_4k"] and pc.version_pinned
    # Removing the pinned version plays the channel itself again, and matching doesn't bring it back.
    assert (await client.delete(f"/api/versions/channels/{ids['f1']}/versions/{ids['f1_4k']}")).status_code == 200
    await client.post(f"/api/versions/playlists/{ids['main']}/match")
    detail = (await client.get(f"/api/versions/channels/{ids['f1']}")).json()
    removed = [v for v in detail["versions"] if v["channel_id"] == ids["f1_4k"]]
    assert removed and removed[0]["excluded"]
    assert detail["channel"]["active_id"] == ids["f1"] and not detail["channel"]["pinned"]
    # An admin adds the ES feed by hand.
    assert (await client.post(f"/api/versions/channels/{ids['f1']}/versions", json={"candidate_channel_id": ids["f1_es"]})).status_code == 200
    detail = (await client.get(f"/api/versions/channels/{ids['f1']}")).json()
    assert any(v["channel_id"] == ids["f1_es"] and v["origin"] == "manual" for v in detail["versions"])


async def test_backup_rules(client, setup):
    ids = setup
    assert (await client.put(f"/api/playlists/{ids['main']}", json={"name": "Main", "backup_for_id": ids["main"]})).status_code == 400
    await client.put(f"/api/playlists/{ids['backup']}", json={"name": "Backup channels", "backup_for_id": ids["main"]})
    assert (await client.put(f"/api/playlists/{ids['main']}", json={"name": "Main", "backup_for_id": ids["backup"]})).status_code == 400
    # An update that doesn't mention it keeps it.
    r = await client.put(f"/api/playlists/{ids['backup']}", json={"name": "Backups"})
    assert r.json()["backup_for_id"] == ids["main"]
