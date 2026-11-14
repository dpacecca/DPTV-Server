"""Versions of a channel, kept by the server (1.0.15).

A backup playlist (Playlist.backup_for_id) holds other copies of a main playlist's channels. After
each sync its channels are matched to the main playlist's; a nightly check plays a sample of every
version (stream_check) and the main channel plays from the best one that really works. Players get
the versions too, to swap by hand or when a stream keeps buffering.

Matching is exact on the normalized name (country prefix and quality tags removed, as the duplicate
scanner and EPG matching do) with no two different countries, or the same guide id with one name's
words all in the other's - the same rules DPTV-Player's Sources list uses.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.base import ChannelType
from app.models.playlist import ChannelVersion, Playlist, PlaylistCategory, PlaylistChannel, VersionSwap
from app.services.name_normalize import extract_country_prefix, normalize_name

logger = logging.getLogger(__name__)

MIN_KEY_LENGTH = 3
PASSES_NEEDED = 2
SWAPS_KEPT = 20

# ---------------------------------------------------------------- matching


@dataclass(frozen=True)
class Facets:
    key: str
    country: str | None
    guide: str | None


def facets(name: str, guide: str | None) -> Facets:
    return Facets(normalize_name(name), extract_country_prefix(name), guide or None)


def _words(key: str) -> set[str]:
    return {w for w in key.split(" ") if len(w) >= 2}


def same_channel(a: Facets, b: Facets) -> bool:
    """The same channel: one guide id and one name's words all in the other's, or the same name key
    (at least 3 letters) with no two different countries ("ES| DAZN 1" is not "AR| DAZN 1")."""
    if a.guide and a.guide == b.guide:
        x, y = _words(a.key), _words(b.key)
        if x and y and (x <= y or y <= x):
            return True
    if len(a.key) < MIN_KEY_LENGTH or a.key != b.key:
        return False
    return a.country is None or b.country is None or a.country == b.country


async def _live_channels(db: AsyncSession, playlist_id: int) -> list[PlaylistChannel]:
    result = await db.execute(
        select(PlaylistChannel)
        .join(PlaylistCategory, PlaylistChannel.playlist_category_id == PlaylistCategory.id)
        .where(PlaylistCategory.playlist_id == playlist_id, PlaylistCategory.channel_type == ChannelType.LIVE)
        .options(selectinload(PlaylistChannel.epg_channel))
    )
    return list(result.scalars().all())


def _facets_of(pc: PlaylistChannel) -> Facets:
    return facets(pc.name, pc.epg_channel.epg_channel_id if pc.epg_channel else None)


async def match_playlist(db: AsyncSession, main: Playlist) -> dict:
    """Matches the main playlist's live channels with its backup playlists' ones. Keeps admin-added
    and admin-removed versions as they are; adds new matches and drops automatic ones that no longer
    match. Returns counts. The caller commits."""
    backups = (await db.execute(select(Playlist).where(Playlist.backup_for_id == main.id))).scalars().all()
    mains = await _live_channels(db, main.id)
    candidates: list[PlaylistChannel] = []
    for b in backups:
        candidates.extend(await _live_channels(db, b.id))
    cand_facets = [(c, _facets_of(c)) for c in candidates]
    by_key: dict[str, list[tuple[PlaylistChannel, Facets]]] = {}
    by_guide: dict[str, list[tuple[PlaylistChannel, Facets]]] = {}
    for c, f in cand_facets:
        by_key.setdefault(f.key, []).append((c, f))
        if f.guide:
            by_guide.setdefault(f.guide, []).append((c, f))

    existing = (
        await db.execute(select(ChannelVersion).where(ChannelVersion.main_channel_id.in_([m.id for m in mains] or [-1])))
    ).scalars().all()
    rows: dict[tuple[int, int], ChannelVersion] = {(r.main_channel_id, r.candidate_channel_id): r for r in existing}
    added = removed = 0
    for m in mains:
        f = _facets_of(m)
        pool = {c.id: (c, cf) for c, cf in by_key.get(f.key, []) + (by_guide.get(f.guide, []) if f.guide else [])}
        matched = {cid for cid, (c, cf) in pool.items() if same_channel(f, cf)}
        for cid in matched:
            if (m.id, cid) not in rows:
                row = ChannelVersion(main_channel_id=m.id, candidate_channel_id=cid, origin="auto", excluded=False, pass_streak=0)
                db.add(row)
                rows[(m.id, cid)] = row
                added += 1
        for (mid, cid), row in list(rows.items()):
            if mid == m.id and row.origin == "auto" and cid not in matched and not row.excluded:
                await db.delete(row)
                del rows[(mid, cid)]
                removed += 1
        has_backup = any(mid == m.id and r.origin != "self" for (mid, _), r in rows.items())
        if has_backup and (m.id, m.id) not in rows:
            row = ChannelVersion(main_channel_id=m.id, candidate_channel_id=m.id, origin="self", excluded=False, pass_streak=0)
            db.add(row)
            rows[(m.id, m.id)] = row
        if not has_backup and m.active_version_id is not None and not m.version_pinned:
            m.active_version_id = None
    logger.info("versions matched for playlist %s: %d added, %d dropped", main.id, added, removed)
    return {"added": added, "removed": removed, "channels": sum(1 for (mid, cid) in rows if mid != cid)}


async def match_all(db: AsyncSession) -> None:
    """Matches every main playlist that has a backup playlist."""
    main_ids = (
        await db.execute(select(Playlist.backup_for_id).where(Playlist.backup_for_id.is_not(None)).distinct())
    ).scalars().all()
    for pid in main_ids:
        main = await db.get(Playlist, pid)
        if main is not None:
            await match_playlist(db, main)


# ---------------------------------------------------------------- choosing


@dataclass
class Choice:
    to_id: int | None
    reason: str


def _rank(r: ChannelVersion) -> tuple:
    return (r.height or 0, r.fps or 0, r.bitrate_kbps or 0)


def choose(rows: list[ChannelVersion], current_id: int, pinned: bool) -> Choice | None:
    """Which version the channel should play from now, or None to keep the current one.

    A version can take over only after passing two checks in a row. It replaces a working current
    version only with a higher picture (a higher resolution, not just a higher bitrate); it replaces
    a failing one with the best that passes. A silent version only stands in for a silent current
    one. A pinned channel never changes."""
    if pinned:
        return None
    usable = [r for r in rows if not r.excluded]
    current = next((r for r in usable if r.candidate_channel_id == current_id), None)
    if current is None:
        return None
    ready = [
        r for r in usable
        if r.candidate_channel_id != current_id and r.ok and r.pass_streak >= PASSES_NEEDED
        and (not r.silent or bool(current.silent))
    ]
    if not ready:
        return None
    best = max(ready, key=_rank)
    if current.ok is False:
        return Choice(best.candidate_channel_id, f"{best.reason}, passed {best.pass_streak} checks; it replaced a version that failed: {current.reason}")
    if current.ok and (best.height or 0) > (current.height or 0):
        return Choice(best.candidate_channel_id, f"{best.reason}, passed {best.pass_streak} checks; better picture than {current.reason}")
    return None


async def apply_choice(db: AsyncSession, main: PlaylistChannel, choice: Choice) -> None:
    """Plays [main] from [choice] and writes the swap to its history (newest SWAPS_KEPT kept)."""
    new_active = None if choice.to_id == main.id else choice.to_id
    db.add(VersionSwap(
        main_channel_id=main.id, from_channel_id=main.active_version_id or main.id,
        to_channel_id=choice.to_id, reason=choice.reason, created_at=datetime.now(timezone.utc),
    ))
    main.active_version_id = new_active
    old = (
        await db.execute(
            select(VersionSwap).where(VersionSwap.main_channel_id == main.id).order_by(VersionSwap.created_at.desc()).offset(SWAPS_KEPT)
        )
    ).scalars().all()
    for o in old:
        await db.delete(o)


_rematch_tasks: set = set()


def rematch_soon() -> None:
    """Matches every backup playlist again in the background (after a sync). Never raises."""
    import asyncio

    from app.db import SessionLocal

    async def run() -> None:
        try:
            async with SessionLocal() as db:
                await match_all(db)
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("matching versions after a sync failed")

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _rematch_tasks.add(task)
    task.add_done_callback(_rematch_tasks.discard)


# ---------------------------------------------------------------- the line being used

_last_stream_at = 0.0


def note_stream() -> None:
    """A player (or recorder) asked for a stream: checks wait while the line may be in use."""
    global _last_stream_at
    _last_stream_at = time.monotonic()


def line_idle(minutes: float) -> bool:
    return time.monotonic() - _last_stream_at > minutes * 60


# ---------------------------------------------------------------- checking


def _url_of(pc: PlaylistChannel) -> str | None:
    """As the stream redirect plays it (a per-user source override aside)."""
    return pc.manual_stream_url or (pc.source_channel.stream_url if pc.source_channel else None)


async def check_channel(db: AsyncSession, main: PlaylistChannel, *, sample_seconds: float, ffmpeg_path: str,
                        idle_minutes: float | None = None) -> str:
    """Checks every version of [main] one at a time, then plays it from the best per [choose].
    With [idle_minutes], stops as soon as a stream was asked for within that time (the line may be in
    use). Returns what happened. The caller commits."""
    from app.services.stream_check import check_stream

    rows = (
        await db.execute(
            select(ChannelVersion)
            .where(ChannelVersion.main_channel_id == main.id, ChannelVersion.excluded.is_(False))
            .options(selectinload(ChannelVersion.candidate).selectinload(PlaylistChannel.source_channel))
        )
    ).scalars().all()
    if len(rows) < 2:
        return "no versions to compare"
    for row in rows:
        if idle_minutes is not None and not line_idle(idle_minutes):
            return "stopped: a stream is in use"
        url = _url_of(row.candidate)
        result = await check_stream(url, sample_seconds, ffmpeg_path) if url else None
        row.checked_at = datetime.now(timezone.utc)
        if result is None:
            row.ok, row.reason, row.pass_streak = False, "no stream address", 0
            continue
        row.ok, row.reason = result.ok, result.reason
        row.height, row.fps, row.bitrate_kbps, row.silent = result.height, result.fps, result.bitrate_kbps, result.silent
        row.pass_streak = row.pass_streak + 1 if result.ok else 0
    choice = choose(list(rows), main.active_version_id or main.id, main.version_pinned)
    if choice is None:
        return "kept"
    await apply_choice(db, main, choice)
    return "swapped"


async def check_all(db_factory, *, sample_seconds: float, ffmpeg_path: str, idle_minutes: float) -> dict:
    """The nightly check: matches, then checks every main channel that has backups, one at a time,
    only while no stream has been asked for in [idle_minutes]. Commits after each channel."""
    counts = {"checked": 0, "swapped": 0, "stopped": 0}
    async with db_factory() as db:
        await match_all(db)
        await db.commit()
        main_ids = (
            await db.execute(
                select(ChannelVersion.main_channel_id)
                .where(ChannelVersion.origin != "self", ChannelVersion.excluded.is_(False))
                .distinct()
            )
        ).scalars().all()
    for mid in main_ids:
        if not line_idle(idle_minutes):
            counts["stopped"] += 1
            logger.info("version check paused: a stream is in use")
            break
        async with db_factory() as db:
            main = await db.get(PlaylistChannel, mid)
            if main is None:
                continue
            outcome = await check_channel(db, main, sample_seconds=sample_seconds, ffmpeg_path=ffmpeg_path, idle_minutes=idle_minutes)
            await db.commit()
        counts["checked"] += 1
        if outcome == "swapped":
            counts["swapped"] += 1
    logger.info("version check: %s", counts)
    return counts
