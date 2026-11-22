"""The recording server advertised to signed-in players (1.0.11)."""

import httpx
import pytest

from app.config import get_settings
from app.main import app
from app.models.xc_user import XcUser

ALICE = {"username": "alice", "password": "pw-a"}


@pytest.fixture
async def users(db):
    db.add(XcUser(username="alice", password="pw-a"))
    await db.commit()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def recorder_url(monkeypatch):
    def set_url(value: str):
        monkeypatch.setenv("DPTV_RECORDER_URL", value)
        get_settings.cache_clear()
    yield set_url
    get_settings.cache_clear()


async def test_a_signed_in_player_is_told_the_recorder(client, users, recorder_url):
    recorder_url("https://dptvr.example.com/")
    r = await client.get("/player_api.php", params=ALICE)
    assert r.status_code == 200
    assert r.json()["server_info"]["dptv_recorder_url"] == "https://dptvr.example.com"


async def test_no_recorder_set_means_no_field(client, users, recorder_url):
    recorder_url("")
    r = await client.get("/player_api.php", params=ALICE)
    assert "dptv_recorder_url" not in r.json()["server_info"]


async def test_a_wrong_password_is_not_told_anything(client, users, recorder_url):
    recorder_url("https://dptvr.example.com")
    r = await client.get("/player_api.php", params={"username": "alice", "password": "nope"})
    assert "dptv_recorder_url" not in r.text


async def test_the_home_address_is_told_beside_the_public_one(client, users, recorder_url, monkeypatch):
    """(1.0.21) Both addresses, the home one tried first by DPTV-Player."""
    monkeypatch.setenv("DPTV_RECORDER_LOCAL_URL", "http://192.168.1.10:3000/")
    recorder_url("https://dptvr.example.com")
    info = (await client.get("/player_api.php", params=ALICE)).json()["server_info"]
    assert info["dptv_recorder_url"] == "https://dptvr.example.com"
    assert info["dptv_recorder_local_url"] == "http://192.168.1.10:3000"
    monkeypatch.setenv("DPTV_RECORDER_LOCAL_URL", "")
    get_settings.cache_clear()
    info = (await client.get("/player_api.php", params=ALICE)).json()["server_info"]
    assert "dptv_recorder_local_url" not in info
