from datetime import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession
from app.models.base import SyncTrigger
from app.models.sync import SyncRun, SyncSchedule
from app.services.sync_engine import run_full_sync

router = APIRouter(prefix="/api", tags=["scheduler"])


class ScheduleIn(BaseModel):
    label: str = ""
    time_of_day: time
    enabled: bool = True
    sync_sources: bool = True
    sync_epg: bool = True


def _serialize_schedule(s: SyncSchedule) -> dict:
    return {
        "id": s.id,
        "label": s.label,
        "time_of_day": s.time_of_day.strftime("%H:%M"),
        "enabled": s.enabled,
        "sync_sources": s.sync_sources,
        "sync_epg": s.sync_epg,
    }


@router.get("/schedules")
async def list_schedules(db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(select(SyncSchedule).order_by(SyncSchedule.time_of_day))
    return [_serialize_schedule(s) for s in result.scalars().all()]


@router.post("/schedules")
async def create_schedule(payload: ScheduleIn, db: DbSession, _admin: AdminUser) -> dict:
    s = SyncSchedule(**payload.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)

    from app.core.scheduler import reload_schedules

    await reload_schedules(db)
    return _serialize_schedule(s)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, db: DbSession, _admin: AdminUser) -> dict:
    s = await db.get(SyncSchedule, schedule_id)
    if s is None:
        raise HTTPException(404, "Schedule not found")
    await db.delete(s)
    await db.commit()

    from app.core.scheduler import reload_schedules

    await reload_schedules(db)
    return {"ok": True}


@router.get("/sync-runs")
async def list_sync_runs(db: DbSession, _admin: AdminUser, limit: int = 20) -> list[dict]:
    result = await db.execute(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit))
    return [
        {
            "id": r.id,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "trigger": r.trigger,
            "status": r.status,
            "summary": r.summary,
        }
        for r in result.scalars().all()
    ]


@router.post("/sync/run")
async def trigger_manual_sync(db: DbSession, _admin: AdminUser) -> dict:
    run = await run_full_sync(db, SyncTrigger.MANUAL)
    await db.commit()
    return {"id": run.id, "status": run.status, "summary": run.summary}
