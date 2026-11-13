"""Matching a single-sign-on person to their XC login (1.0.13), for DPTV Web.

DPTV Web signs people in through Authentik. To play, it needs that person's XC login here. With the
same key on both sides (Settings > Integrations > "DPTV Web link key" here, DPTV_SERVER_LINK_KEY on
DPTV Web), DPTV Web asks for the login whose username matches the Authentik username, and nobody has
to type it after signing in. Off while no key is set. The password is never logged.
"""

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.config import get_settings
from app.models.xc_user import XcUser

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# Shorter than this is too easy to guess; such a key leaves the lookup off.
MIN_KEY_LENGTH = 24


def _key_ok(authorization: str | None) -> bool:
    key = get_settings().web_link_key.strip()
    if len(key) < MIN_KEY_LENGTH:
        raise HTTPException(404, "Not turned on")
    sent = (authorization or "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(sent.encode(), key.encode())


@router.get("/xc-login")
async def xc_login_for(username: str, db: DbSession, authorization: str | None = Header(default=None)) -> dict:
    if not _key_ok(authorization):
        raise HTTPException(401, "Wrong key")
    name = username.strip()
    if not name:
        raise HTTPException(404, "No such login")
    # The exact name first; else one, and only one, that matches ignoring case.
    user = (await db.execute(select(XcUser).where(XcUser.username == name))).scalar_one_or_none()
    if user is None:
        found = (await db.execute(select(XcUser).where(func.lower(XcUser.username) == name.lower()))).scalars().all()
        user = found[0] if len(found) == 1 else None
    if user is None or not user.enabled:
        raise HTTPException(404, "No such login")
    expiry = user.expiry_date
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            raise HTTPException(404, "No such login")
    return {"username": user.username, "password": user.password}
