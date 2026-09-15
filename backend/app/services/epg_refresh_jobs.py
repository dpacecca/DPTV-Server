import asyncio
import logging
import secrets
from dataclasses import dataclass

from app.db import SessionLocal
from app.models.base import SyncStatus
from app.models.epg import EpgSource
from app.services.sync_engine import sync_all_epg_sources, sync_epg_source

logger = logging.getLogger("dptv.epg_refresh_jobs")

_MAX_JOBS = 50


@dataclass
class EpgRefreshJob:
    id: str
    status: str = "running"
    """running | done | error"""
    error: str | None = None
    result: dict | None = None
    """Once done: {"channels": N, "programs": N} for a single source, or
    {"epg_sources": {name: {...}}, "errors": [...]} for a refresh-all."""


_jobs: dict[str, EpgRefreshJob] = {}


def get_job(job_id: str) -> EpgRefreshJob | None:
    return _jobs.get(job_id)


def _new_job() -> EpgRefreshJob:
    job_id = secrets.token_hex(8)
    job = EpgRefreshJob(id=job_id)
    _jobs[job_id] = job

    if len(_jobs) > _MAX_JOBS:
        for old_id in list(_jobs)[: len(_jobs) - _MAX_JOBS]:
            _jobs.pop(old_id, None)

    return job


async def _run_single(job_id: str, epg_source_id: int) -> None:
    job = _jobs[job_id]
    try:
        async with SessionLocal() as db:
            epg = await db.get(EpgSource, epg_source_id)
            if epg is None:
                raise RuntimeError("EPG source not found")
            summary = await sync_epg_source(db, epg)
            await db.commit()
        job.result = summary
        job.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("EPG refresh job %s (source %d) failed", job_id, epg_source_id)
        job.status = "error"
        job.error = str(exc)
        # sync_epg_source only records success on the row itself (see its own
        # last_refresh_status write) - record the failure too so the EPG Sources table's status
        # badge reflects reality instead of showing whatever the last successful/failed refresh
        # (possibly from a scheduled full sync) happened to leave behind.
        try:
            async with SessionLocal() as db:
                epg = await db.get(EpgSource, epg_source_id)
                if epg is not None:
                    epg.last_refresh_status = SyncStatus.FAILED.value
                    epg.last_refresh_error = str(exc)
                    await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record refresh failure for EPG source %d", epg_source_id)


async def _run_all(job_id: str) -> None:
    job = _jobs[job_id]
    try:
        async with SessionLocal() as db:
            summary = await sync_all_epg_sources(db)
            await db.commit()
        job.result = summary
        job.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("EPG refresh-all job %s failed", job_id)
        job.status = "error"
        job.error = str(exc)


def start_single_refresh(epg_source_id: int) -> EpgRefreshJob:
    """Refreshing a source - especially an iptv-org one scraping a slow/chatty broadcaster site
    - can take many minutes were it to run synchronously in the request; that's long enough to
    outlive any reverse proxy or tunnel in front of this app, producing a spurious client-side
    failure even though the backend keeps working. So this only kicks the work off in the
    background; poll GET /refresh-jobs/{job_id} for progress."""
    job = _new_job()
    asyncio.create_task(_run_single(job.id, epg_source_id))
    return job


def start_all_refresh() -> EpgRefreshJob:
    job = _new_job()
    asyncio.create_task(_run_all(job.id))
    return job
