"""The player profile kept for each XC login (1.0.9): see app.models.xc_user.XcUserProfile.

The server stores the profile as the player sent it and never reads inside it; merging is the
player's job (it already merges two devices' data for its own Local sync). A write names the
revision it was merged from and only lands when that is still the current one - a compare-and-set
in one UPDATE, so two TVs writing at once can't both win and lose the other's changes.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.xc_user import XcUser, XcUserProfile

MAX_PROFILE_BYTES = 8 * 1024 * 1024
"""A profile is history, progress and favourites - kilobytes to a few megabytes. Anything bigger
is refused rather than stored, so one misbehaving client can't fill the database."""


@dataclass
class Stored:
    revision: int
    updated_at: datetime | None
    device: str | None
    data: str | None


class StaleRevision(Exception):
    def __init__(self, current: int):
        super().__init__(f"profile is at revision {current}")
        self.current = current


async def read(db: AsyncSession, user: XcUser) -> Stored:
    return await _read(db, user.id)


async def _read(db: AsyncSession, user_id: int) -> Stored:
    row = (await db.execute(select(XcUserProfile).where(XcUserProfile.xc_user_id == user_id))).scalar_one_or_none()
    if row is None:
        return Stored(0, None, None, None)
    return Stored(row.revision, row.updated_at, row.device, row.data)


async def write(db: AsyncSession, user: XcUser, base_revision: int, data: object, device: str | None) -> Stored:
    """Store [data] as revision base_revision + 1, or raise [StaleRevision] when another TV wrote first."""
    # The id now: a rollback below expires [user], and reading it then would be IO outside await.
    user_id = user.id
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ValueError("profile too large")
    now = datetime.now(timezone.utc)
    device = (device or "").strip()[:255] or None
    if base_revision == 0:
        db.add(XcUserProfile(xc_user_id=user_id, data=raw, revision=1, updated_at=now, device=device))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise StaleRevision((await _read(db, user_id)).revision)
        return Stored(1, now, device, raw)
    result = await db.execute(
        update(XcUserProfile)
        .where(XcUserProfile.xc_user_id == user_id, XcUserProfile.revision == base_revision)
        .values(data=raw, revision=base_revision + 1, updated_at=now, device=device)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise StaleRevision((await _read(db, user_id)).revision)
    await db.commit()
    return Stored(base_revision + 1, now, device, raw)


async def clear(db: AsyncSession, user_id: int) -> bool:
    row = (await db.execute(select(XcUserProfile).where(XcUserProfile.xc_user_id == user_id))).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True
