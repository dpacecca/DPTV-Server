"""The player profile kept per XC login (1.0.9): read, write, stale writes refused, admin view."""

import httpx
import pytest

from app.main import app
from app.models.xc_user import XcUser

ALICE = {"username": "alice", "password": "pw-a"}
BOB = {"username": "bob", "password": "pw-b"}


@pytest.fixture
async def users(db):
    db.add_all([XcUser(username="alice", password="pw-a"), XcUser(username="bob", password="pw-b")])
    await db.commit()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _put(client, creds, base, data, device="Living room"):
    return await client.post(
        "/player_api.php", params={**creds, "action": "dptv_profile_put"},
        json={"base_revision": base, "data": data, "device": device},
    )


async def test_an_empty_profile_reads_as_revision_zero(client, users):
    r = await client.get("/player_api.php", params={**ALICE, "action": "dptv_profile"})
    assert r.status_code == 200
    assert r.json() == {"revision": 0, "updated_at": None, "device": None, "data": None}


async def test_write_then_read_back(client, users):
    r = await _put(client, ALICE, 0, {"history": [1, 2], "name": "Zoë"})
    assert r.status_code == 200 and r.json()["revision"] == 1
    got = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_profile"})).json()
    assert got["revision"] == 1 and got["device"] == "Living room"
    assert got["data"] == {"history": [1, 2], "name": "Zoë"}


async def test_a_stale_write_is_refused_with_the_current_revision(client, users):
    assert (await _put(client, ALICE, 0, {"v": 1})).status_code == 200
    assert (await _put(client, ALICE, 1, {"v": 2}, device="Bedroom")).status_code == 200
    stale = await _put(client, ALICE, 1, {"v": "lost"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["revision"] == 2
    first_again = await _put(client, ALICE, 0, {"v": "lost"})
    assert first_again.status_code == 409
    got = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_profile"})).json()
    assert got["data"] == {"v": 2} and got["device"] == "Bedroom"


async def test_each_login_has_its_own_profile(client, users):
    await _put(client, ALICE, 0, {"who": "alice"})
    got = (await client.get("/player_api.php", params={**BOB, "action": "dptv_profile"})).json()
    assert got["revision"] == 0 and got["data"] is None


async def test_sync_status_carries_the_profile_revision(client, users):
    before = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_sync_status"})).json()
    assert before["profile"]["rev"] == "0"
    await _put(client, ALICE, 0, {"x": 1})
    after = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_sync_status"})).json()
    assert after["profile"]["rev"] == "1" and after["profile"]["updated_at"]


async def test_profile_needs_the_login(client, users):
    r = await client.get("/player_api.php", params={"username": "alice", "password": "no", "action": "dptv_profile"})
    assert r.status_code == 401
    r = await _put(client, {"username": "alice", "password": "no"}, 0, {"x": 1})
    assert r.status_code == 401


async def test_only_the_profile_write_is_a_post(client, users):
    r = await client.post("/player_api.php", params={**ALICE, "action": "get_live_streams"}, json={"base_revision": 0, "data": {}})
    assert r.status_code == 400


async def test_admin_sees_and_clears_a_profile(client, users):
    from app.api.deps import require_admin

    app.dependency_overrides[require_admin] = lambda: "admin"
    try:
        await _put(client, ALICE, 0, {"x": 1}, device="Kitchen")
        listed = {u["username"]: u for u in (await client.get("/api/xc-users")).json()}
        assert listed["alice"]["profile"]["device"] == "Kitchen" and listed["alice"]["profile"]["revision"] == 1
        assert listed["bob"]["profile"] is None
        assert (await client.delete(f"/api/xc-users/{listed['alice']['id']}/profile")).json() == {"cleared": True}
        got = (await client.get("/player_api.php", params={**ALICE, "action": "dptv_profile"})).json()
        assert got["revision"] == 0
        # A TV that still holds revision 1 is told to start again from 0.
        assert (await _put(client, ALICE, 1, {"x": 2})).status_code == 409
    finally:
        app.dependency_overrides.pop(require_admin, None)
