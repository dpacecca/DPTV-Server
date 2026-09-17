from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.models.epg import EpgChannel, EpgSource
from app.services import epg_refresh_jobs

router = APIRouter(prefix="/api/epg-sources", tags=["epg-sources"])


class EpgSourceIn(BaseModel):
    name: str
    url: str
    refresh_interval_minutes: int = 720


def _serialize(epg: EpgSource, channel_count: int = 0) -> dict:
    return {
        "id": epg.id,
        "name": epg.name,
        "url": epg.url,
        "refresh_interval_minutes": epg.refresh_interval_minutes,
        "last_refreshed_at": epg.last_refreshed_at.isoformat() if epg.last_refreshed_at else None,
        "last_refresh_status": epg.last_refresh_status,
        "last_refresh_error": epg.last_refresh_error,
        "channel_count": channel_count,
    }


@router.get("")
async def list_epg_sources(db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(
        select(EpgSource, func.count(EpgChannel.id))
        .outerjoin(EpgChannel, EpgChannel.epg_source_id == EpgSource.id)
        .group_by(EpgSource.id)
        .order_by(EpgSource.name)
    )
    return [_serialize(e, count) for e, count in result.all()]


@router.post("")
async def create_epg_source(payload: EpgSourceIn, db: DbSession, _admin: AdminUser) -> dict:
    epg = EpgSource(
        name=payload.name,
        url=payload.url,
        refresh_interval_minutes=payload.refresh_interval_minutes,
    )
    db.add(epg)
    await db.commit()
    await db.refresh(epg)
    return _serialize(epg)


@router.patch("/{epg_source_id}")
async def update_epg_source(epg_source_id: int, payload: EpgSourceIn, db: DbSession, _admin: AdminUser) -> dict:
    epg = await db.get(EpgSource, epg_source_id)
    if epg is None:
        raise HTTPException(404, "EPG source not found")
    epg.name = payload.name
    epg.url = payload.url
    epg.refresh_interval_minutes = payload.refresh_interval_minutes
    await db.commit()
    await db.refresh(epg)
    return _serialize(epg)


@router.delete("/{epg_source_id}")
async def delete_epg_source(epg_source_id: int, db: DbSession, _admin: AdminUser) -> dict:
    epg = await db.get(EpgSource, epg_source_id)
    if epg is None:
        raise HTTPException(404, "EPG source not found")
    await db.delete(epg)
    await db.commit()
    return {"ok": True}


@router.post("/refresh-all")
async def refresh_all_epg_sources(_admin: AdminUser) -> dict:
    """Kicks off a background refresh of every EPG source and returns immediately - fetching and
    parsing a large XMLTV feed can take a while, far longer than this request should stay open,
    so the actual work happens in the background. Poll GET /refresh-jobs/{job_id} for progress;
    once done, playlists reflect the new guide data right away (read live from EpgChannel/
    EpgProgram, not cached per playlist). This does not re-run EPG auto-mapping for newly-added
    channels or auto-clear; use Scheduler's "Sync Now" for the full pass."""
    job = epg_refresh_jobs.start_all_refresh()
    return {"job_id": job.id}


@router.post("/{epg_source_id}/refresh")
async def refresh_epg_source(epg_source_id: int, db: DbSession, _admin: AdminUser) -> dict:
    """Kicks off a background refresh of this one source and returns immediately - see
    refresh-all above for why. Poll GET /refresh-jobs/{job_id} for progress."""
    epg = await db.get(EpgSource, epg_source_id)
    if epg is None:
        raise HTTPException(404, "EPG source not found")
    job = epg_refresh_jobs.start_single_refresh(epg_source_id)
    return {"job_id": job.id}


@router.get("/refresh-jobs/{job_id}")
async def get_epg_refresh_job(job_id: str, _admin: AdminUser) -> dict:
    job = epg_refresh_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Refresh job not found")
    return {"job_id": job.id, "status": job.status, "error": job.error, "result": job.result}
