from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.models.epg import EpgChannel, EpgSource
from app.services.sync_engine import sync_epg_source

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
    epg = EpgSource(**payload.model_dump())
    db.add(epg)
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


@router.post("/{epg_source_id}/refresh")
async def refresh_epg_source(epg_source_id: int, db: DbSession, _admin: AdminUser) -> dict:
    epg = await db.get(EpgSource, epg_source_id)
    if epg is None:
        raise HTTPException(404, "EPG source not found")
    try:
        summary = await sync_epg_source(db, epg)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(502, f"Refresh failed: {exc}") from exc
    return summary
