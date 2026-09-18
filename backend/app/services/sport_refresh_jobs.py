import asyncio
import logging
import secrets
from dataclasses import dataclass

from app.db import SessionLocal
from app.models.playlist import PlaylistCategory
from app.services.sport_refresh import refresh_sport_category

logger = logging.getLogger("dptv.sport_refresh_jobs")

_MAX_JOBS = 50


@dataclass
class SportRefreshJob:
    id: str
    status: str = "running"
    """running | done | error"""
    error: str | None = None


_jobs: dict[str, SportRefreshJob] = {}


def get_job(job_id: str) -> SportRefreshJob | None:
    return _jobs.get(job_id)


def _new_job() -> SportRefreshJob:
    job_id = secrets.token_hex(8)
    job = SportRefreshJob(id=job_id)
    _jobs[job_id] = job

    if len(_jobs) > _MAX_JOBS:
        for old_id in list(_jobs)[: len(_jobs) - _MAX_JOBS]:
            _jobs.pop(old_id, None)

    return job


async def _run(job_id: str, category_id: int) -> None:
    job = _jobs[job_id]
    try:
        async with SessionLocal() as db:
            category = await db.get(PlaylistCategory, category_id)
            if category is None or category.sport_type is None:
                raise RuntimeError("Sport category not found")
            await refresh_sport_category(db, category)
            await db.commit()
        # refresh_sport_category records its own success/failure on the category row (mirroring
        # an EPG source's last_refresh_status) - re-raise here too so the poller's job.status
        # reflects a failed fetch instead of reporting "done" for a refresh that changed nothing.
        async with SessionLocal() as db:
            category = await db.get(PlaylistCategory, category_id)
            if category is not None and category.sport_last_refresh_status == "failed":
                raise RuntimeError(category.sport_last_refresh_error or "Refresh failed")
        job.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sport refresh job %s (category %d) failed", job_id, category_id)
        job.status = "error"
        job.error = str(exc)


def start_refresh(category_id: int) -> SportRefreshJob:
    """Kicks off a background refresh of one Live Sport category (fetches fixtures, matches
    channels, repopulates) - poll GET /sport-refresh-jobs/{job_id} for progress, same pattern as
    EPG source manual refresh (see epg_refresh_jobs.py)."""
    job = _new_job()
    asyncio.create_task(_run(job.id, category_id))
    return job
