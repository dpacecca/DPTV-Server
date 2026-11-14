"""Admin: versions of a channel from its backup playlists (1.0.15). See app.services.channel_versions."""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DbSession
from app.config import get_settings
from app.db import SessionLocal
from app.models.playlist import ChannelVersion, Playlist, PlaylistCategory, PlaylistChannel, VersionSwap
from app.services.channel_versions import Choice, apply_choice, check_channel, match_playlist

router = APIRouter(prefix="/api/versions", tags=["versions"])
logger = logging.getLogger(__name__)

# One check at a time, whoever asks: the provider's line may allow a single connection.
_check_lock = asyncio.Lock()
_checking: set[int] = set()
_tasks: set[asyncio.Task] = set()


def _ts(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


async def _playlist_of(db, pc: PlaylistChannel) -> Playlist | None:
    cat = await db.get(PlaylistCategory, pc.playlist_category_id)
    return await db.get(Playlist, cat.playlist_id) if cat else None


async def _main_channel(db, channel_id: int) -> PlaylistChannel:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    return pc


@router.get("/playlists/{playlist_id}/summary")
async def playlist_summary(playlist_id: int, db: DbSession, _admin: AdminUser) -> dict:
    """Per main channel with versions: how many, which plays, pinned, and whether that one passed."""
    rows = (
        await db.execute(
            select(ChannelVersion, PlaylistChannel)
            .join(PlaylistChannel, ChannelVersion.main_channel_id == PlaylistChannel.id)
            .join(PlaylistCategory, PlaylistChannel.playlist_category_id == PlaylistCategory.id)
            .where(PlaylistCategory.playlist_id == playlist_id, ChannelVersion.excluded.is_(False))
        )
    ).all()
    out: dict[str, dict] = {}
    for v, main in rows:
        entry = out.setdefault(str(main.id), {"versions": 0, "active_id": main.active_version_id or main.id, "pinned": main.version_pinned, "ok": None})
        entry["versions"] += 1
        if v.candidate_channel_id == entry["active_id"]:
            entry["ok"] = v.ok
    return {k: v for k, v in out.items() if v["versions"] > 1}


@router.post("/playlists/{playlist_id}/match")
async def match_now(playlist_id: int, db: DbSession, _admin: AdminUser) -> dict:
    pl = await db.get(Playlist, playlist_id)
    if pl is None:
        raise HTTPException(404, "Playlist not found")
    if pl.backup_for_id is not None:
        pl = await db.get(Playlist, pl.backup_for_id)
    counts = await match_playlist(db, pl)
    await db.commit()
    return counts


@router.get("/channels/{channel_id}")
async def channel_versions(channel_id: int, db: DbSession, _admin: AdminUser) -> dict:
    main = await _main_channel(db, channel_id)
    rows = (
        await db.execute(
            select(ChannelVersion)
            .where(ChannelVersion.main_channel_id == main.id)
            .options(selectinload(ChannelVersion.candidate))
        )
    ).scalars().all()
    playlists = {}
    for r in rows:
        pl = await _playlist_of(db, r.candidate)
        playlists[r.candidate_channel_id] = pl.name if pl else None
    swaps = (
        await db.execute(select(VersionSwap).where(VersionSwap.main_channel_id == main.id).order_by(VersionSwap.created_at.desc()))
    ).scalars().all()
    names = {r.candidate_channel_id: r.candidate.name for r in rows}
    active = main.active_version_id or main.id
    return {
        "channel": {"id": main.id, "name": main.name, "active_id": active, "pinned": main.version_pinned, "checking": main.id in _checking},
        "versions": [
            {
                "channel_id": r.candidate_channel_id, "name": r.candidate.name, "playlist": playlists.get(r.candidate_channel_id),
                "origin": r.origin, "excluded": r.excluded, "active": r.candidate_channel_id == active,
                "ok": r.ok, "reason": r.reason, "height": r.height, "fps": r.fps, "silent": r.silent,
                "pass_streak": r.pass_streak, "checked_at": _ts(r.checked_at),
            }
            for r in sorted(rows, key=lambda r: (r.origin != "self", r.excluded, -(r.height or 0)))
        ],
        "swaps": [
            {"at": _ts(s.created_at), "from": names.get(s.from_channel_id, s.from_channel_id), "to": names.get(s.to_channel_id, s.to_channel_id), "reason": s.reason}
            for s in swaps
        ],
    }


@router.get("/channels/{channel_id}/candidates")
async def candidates(channel_id: int, db: DbSession, _admin: AdminUser, q: str = "") -> list[dict]:
    """Channels in the main playlist's backup playlists whose name contains [q], to add by hand."""
    main = await _main_channel(db, channel_id)
    pl = await _playlist_of(db, main)
    if pl is None:
        return []
    backups = (await db.execute(select(Playlist.id).where(Playlist.backup_for_id == pl.id))).scalars().all()
    if not backups:
        return []
    stmt = (
        select(PlaylistChannel, Playlist.name)
        .join(PlaylistCategory, PlaylistChannel.playlist_category_id == PlaylistCategory.id)
        .join(Playlist, PlaylistCategory.playlist_id == Playlist.id)
        .where(PlaylistCategory.playlist_id.in_(backups))
        .order_by(PlaylistChannel.name)
        .limit(50)
    )
    if q.strip():
        stmt = stmt.where(func.lower(PlaylistChannel.name).contains(q.strip().lower()))
    return [{"channel_id": c.id, "name": c.name, "playlist": name} for c, name in (await db.execute(stmt)).all()]


class VersionIn(BaseModel):
    candidate_channel_id: int


@router.post("/channels/{channel_id}/versions")
async def add_version(channel_id: int, payload: VersionIn, db: DbSession, _admin: AdminUser) -> dict:
    main = await _main_channel(db, channel_id)
    if await db.get(PlaylistChannel, payload.candidate_channel_id) is None:
        raise HTTPException(404, "That channel doesn't exist")
    row = (
        await db.execute(
            select(ChannelVersion).where(
                ChannelVersion.main_channel_id == main.id, ChannelVersion.candidate_channel_id == payload.candidate_channel_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(ChannelVersion(main_channel_id=main.id, candidate_channel_id=payload.candidate_channel_id, origin="manual", excluded=False, pass_streak=0))
    else:
        row.excluded = False
        row.origin = "manual" if row.origin == "auto" else row.origin
    has_self = (
        await db.execute(select(ChannelVersion).where(ChannelVersion.main_channel_id == main.id, ChannelVersion.candidate_channel_id == main.id))
    ).scalar_one_or_none()
    if has_self is None and payload.candidate_channel_id != main.id:
        db.add(ChannelVersion(main_channel_id=main.id, candidate_channel_id=main.id, origin="self", excluded=False, pass_streak=0))
    await db.commit()
    return {"ok": True}


@router.delete("/channels/{channel_id}/versions/{candidate_id}")
async def remove_version(channel_id: int, candidate_id: int, db: DbSession, _admin: AdminUser) -> dict:
    """Removes a version; a matched one stays removed when the playlists are matched again."""
    main = await _main_channel(db, channel_id)
    if candidate_id == main.id:
        raise HTTPException(400, "The channel itself is always one of its versions")
    row = (
        await db.execute(
            select(ChannelVersion).where(ChannelVersion.main_channel_id == main.id, ChannelVersion.candidate_channel_id == candidate_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Not a version of this channel")
    if row.origin == "manual":
        await db.delete(row)
    else:
        row.excluded = True
    if main.active_version_id == candidate_id:
        main.active_version_id = None
        main.version_pinned = False
    await db.commit()
    return {"ok": True}


class PinIn(BaseModel):
    candidate_channel_id: int | None = None
    """The version to play from, kept until unpinned; None unpins (the check chooses again)."""


@router.put("/channels/{channel_id}/pin")
async def pin(channel_id: int, payload: PinIn, db: DbSession, _admin: AdminUser) -> dict:
    main = await _main_channel(db, channel_id)
    if payload.candidate_channel_id is None:
        main.version_pinned = False
        await db.commit()
        return {"ok": True}
    row = (
        await db.execute(
            select(ChannelVersion).where(
                ChannelVersion.main_channel_id == main.id, ChannelVersion.candidate_channel_id == payload.candidate_channel_id,
                ChannelVersion.excluded.is_(False),
            )
        )
    ).scalar_one_or_none()
    if row is None and payload.candidate_channel_id != main.id:
        raise HTTPException(400, "Not a version of this channel")
    if (main.active_version_id or main.id) != payload.candidate_channel_id:
        main.version_pinned = False
        await apply_choice(db, main, Choice(payload.candidate_channel_id, "pinned by an admin"))
    main.version_pinned = True
    await db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/check")
async def check_now(channel_id: int, db: DbSession, _admin: AdminUser) -> dict:
    """Checks every version of the channel now, in the background, one at a time. Uses the line even
    if someone is watching: the admin asked for it."""
    main = await _main_channel(db, channel_id)
    if main.id in _checking:
        return {"started": False, "checking": True}
    cfg = get_settings()
    _checking.add(main.id)

    async def run(mid: int) -> None:
        try:
            async with _check_lock, SessionLocal() as session:
                pc = await session.get(PlaylistChannel, mid)
                if pc is not None:
                    await check_channel(
                        session, pc, sample_seconds=cfg.versions_sample_seconds,
                        ffmpeg_path=cfg.ffprobe_path.replace("ffprobe", "ffmpeg") or "ffmpeg",
                    )
                    await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("checking versions of channel %s failed", mid)
        finally:
            _checking.discard(mid)

    task = asyncio.get_running_loop().create_task(run(main.id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"started": True, "at": int(datetime.now(timezone.utc).timestamp())}
