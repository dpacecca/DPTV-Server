"""DPTV Web's lookup of a person's XC login by their single-sign-on username (1.0.13)."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import get_settings
from app.main import app
from app.models.xc_user import XcUser

KEY = "k" * 32


@pytest.fixture
async def users(db):
    db.add(XcUser(username="Damian", password="pw-d"))
    db.add(XcUser(username="off", password="pw-o", enabled=False))
    db.add(XcUser(username="old", password="pw-x", expiry_date=datetime.now(timezone.utc) - timedelta(days=1)))
    await db.commit()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def link_key(monkeypatch):
    def set_key(value: str):
        monkeypatch.setenv("DPTV_WEB_LINK_KEY", value)
        get_settings.cache_clear()
    yield set_key
    get_settings.cache_clear()


def ask(client, username, key=KEY):
    return client.get("/api/integrations/xc-login", params={"username": username}, headers={"Authorization": f"Bearer {key}"})


async def test_off_without_a_key(client, users, link_key):
    link_key("")
    assert (await ask(client, "Damian")).status_code == 404
    link_key("short")
    assert (await ask(client, "Damian", "short")).status_code == 404


async def test_the_matching_login_is_given_with_the_key(client, users, link_key):
    link_key(KEY)
    r = await ask(client, "Damian")
    assert r.status_code == 200
    assert r.json() == {"username": "Damian", "password": "pw-d"}
    # Authentik usernames are often lower case: the one login matching ignoring case.
    assert (await ask(client, "damian")).json()["username"] == "Damian"


async def test_a_wrong_key_gets_nothing(client, users, link_key):
    link_key(KEY)
    r = await ask(client, "Damian", "x" * 32)
    assert r.status_code == 401
    assert "pw-d" not in r.text
    assert (await client.get("/api/integrations/xc-login", params={"username": "Damian"})).status_code == 401


async def test_unknown_disabled_or_expired_logins_are_not_given(client, users, link_key):
    link_key(KEY)
    for name in ("nobody", "off", "old", ""):
        assert (await ask(client, name)).status_code == 404
