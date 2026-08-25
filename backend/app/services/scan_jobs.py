import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.models.playlist import PlaylistChannel
from app.services import duplicate_scanner, stream_prober

logger = logging.getLogger("dptv.scan_jobs")

_MAX_JOBS = 50


@dataclass
class ScanJob:
    id: str
    playlist_id: int
    category_id: int
    total: int
    completed: int = 0
    status: str = "running"
    """running | done | error"""
    error: str | None = None
    results: list[dict] = field(default_factory=list)
    duplicate_groups: list[dict] = field(default_factory=list)


_jobs: dict[str, ScanJob] = {}


def get_job(job_id: str) -> ScanJob | None:
    return _jobs.get(job_id)


def _resolve_url(pc: PlaylistChannel) -> str | None:
    """Same resolution order the XC pass-through redirect uses (see xc_server._resolve_and_redirect)."""
    return pc.manual_stream_url or (pc.source_channel.stream_url if pc.source_channel else None)


async def _probe_one(channel_id: int, name: str, url: str | None, timeout_seconds: float, semaphore: asyncio.Semaphore) -> dict:
    if not url:
        probe = stream_prober.ProbeResult(status="no_url", error="No stream URL")
    else:
        async with semaphore:
            probe = await stream_prober.probe_stream(
                url, timeout_seconds=timeout_seconds, ffprobe_path=get_settings().ffprobe_path
            )
    return {
        "channel_id": channel_id,
        "name": name,
        "status": probe.status,
        "width": probe.width,
        "height": probe.height,
        "fps": probe.fps,
        "bitrate_kbps": probe.bitrate_kbps,
        "error": probe.error,
        "resolution_label": duplicate_scanner.resolution_label(probe.height),
    }


async def _run(job_id: str, channel_ids: list[int], concurrency: int, timeout_seconds: float) -> None:
    job = _jobs[job_id]
    try:
        async with SessionLocal() as db:
            result = await db.execute(
                select(PlaylistChannel)
                .where(PlaylistChannel.id.in_(channel_ids))
                .options(selectinload(PlaylistChannel.source_channel))
            )
            channels = {pc.id: pc for pc in result.scalars().all()}

            semaphore = asyncio.Semaphore(max(1, concurrency))
            results: list[dict] = []
            for coro in asyncio.as_completed(
                [
                    _probe_one(cid, pc.name, _resolve_url(pc), timeout_seconds, semaphore)
                    for cid, pc in channels.items()
                ]
            ):
                res = await coro
                results.append(res)
                job.completed += 1

            now = datetime.now(timezone.utc)
            for res in results:
                pc = channels.get(res["channel_id"])
                if pc is None:
                    continue
                pc.detected_width = res["width"]
                pc.detected_height = res["height"]
                pc.detected_fps = res["fps"]
                pc.detected_bitrate_kbps = res["bitrate_kbps"]
                pc.probe_status = res["status"]
                pc.last_probed_at = now
            await db.commit()

        job.results = results
        job.duplicate_groups = duplicate_scanner.group_duplicates(results)
        job.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Duplicate scan job %s failed", job_id)
        job.status = "error"
        job.error = str(exc)


def start_scan_job(playlist_id: int, category_id: int, channel_ids: list[int], concurrency: int, timeout_seconds: float) -> ScanJob:
    job_id = secrets.token_hex(8)
    job = ScanJob(id=job_id, playlist_id=playlist_id, category_id=category_id, total=len(channel_ids))
    _jobs[job_id] = job

    if len(_jobs) > _MAX_JOBS:
        for old_id in list(_jobs)[: len(_jobs) - _MAX_JOBS]:
            _jobs.pop(old_id, None)

    asyncio.create_task(_run(job_id, channel_ids, concurrency, timeout_seconds))
    return job
