import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.config import get_settings
from app.models.epg import EpgChannel, EpgSource
from app.services import iptv_org_epg
from app.services.sync_engine import sync_all_epg_sources, sync_epg_source

router = APIRouter(prefix="/api/epg-sources", tags=["epg-sources"])


class IptvOrgSelectionIn(BaseModel):
    mode: str
    """"country", "category", "channels", or "mapped"."""
    values: list[str] = []
    """Required (non-empty) for "country"/"category"/"channels". Unused for "mapped" - that
    mode re-resolves against whichever channels are currently mapped (PlaylistChannel.
    iptv_org_channel_id) at refresh time, across every playlist, instead of a fixed list."""


class EpgSourceIn(BaseModel):
    name: str
    source_kind: str = "url"
    url: str | None = None
    iptv_org_selection: IptvOrgSelectionIn | None = None
    refresh_interval_minutes: int = 720

    @model_validator(mode="after")
    def _validate_kind(self) -> "EpgSourceIn":
        if self.source_kind == "url":
            if not self.url:
                raise ValueError("url is required for source_kind='url'")
        elif self.source_kind == "iptv_org":
            if self.iptv_org_selection is None:
                raise ValueError("iptv_org_selection is required for source_kind='iptv_org'")
            if self.iptv_org_selection.mode not in ("country", "category", "channels", "mapped"):
                raise ValueError("iptv_org_selection.mode must be 'country', 'category', 'channels', or 'mapped'")
            if self.iptv_org_selection.mode != "mapped" and not self.iptv_org_selection.values:
                raise ValueError(f"iptv_org_selection.values is required for mode={self.iptv_org_selection.mode!r}")
        else:
            raise ValueError(f"Unknown source_kind: {self.source_kind!r}")
        return self


def _serialize(epg: EpgSource, channel_count: int = 0) -> dict:
    return {
        "id": epg.id,
        "name": epg.name,
        "source_kind": epg.source_kind,
        "url": epg.url,
        "iptv_org_selection": json.loads(epg.iptv_org_selection) if epg.iptv_org_selection else None,
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


@router.get("/iptv-org/catalog")
async def get_iptv_org_catalog(_admin: AdminUser) -> dict:
    """Countries/categories the vendored iptv-org/epg checkout can currently scrape. Returns
    available=False (with empty lists) if the server hasn't been set up with the optional
    Node.js + checkout dependency (DPTV_IPTV_ORG_EPG_DIR)."""
    settings = get_settings()
    if not settings.iptv_org_epg_dir:
        return {"available": False, "countries": [], "categories": []}

    countries = await iptv_org_epg.list_countries()
    categories = await iptv_org_epg.list_categories()
    return {
        "available": True,
        "countries": [
            {"name": c.name, "channel_count": c.channel_count, "matched_channel_count": c.matched_channel_count}
            for c in countries
        ],
        "categories": [{"id": c.id, "name": c.name, "channel_count": c.channel_count} for c in categories],
    }


@router.get("/iptv-org/search-channels")
async def search_iptv_org_channels(q: str, _admin: AdminUser) -> dict:
    """Search-as-you-type channel picker, for the "specific channels" selection mode - lets an
    admin pick exactly the channels they use instead of pulling a whole country/category.
    Empty results (not an error) if the scraper isn't configured or the query is blank."""
    settings = get_settings()
    if not settings.iptv_org_epg_dir:
        return {"available": False, "results": []}

    results = await iptv_org_epg.search_channels(q)
    return {
        "available": True,
        "results": [
            {
                "id": r.id,
                "name": r.name,
                "country": r.country,
                "categories": list(r.categories),
                "site_count": r.site_count,
            }
            for r in results
        ],
    }


@router.post("")
async def create_epg_source(payload: EpgSourceIn, db: DbSession, _admin: AdminUser) -> dict:
    if payload.source_kind == "iptv_org" and not get_settings().iptv_org_epg_dir:
        raise HTTPException(400, "iptv-org/epg is not configured on this server (DPTV_IPTV_ORG_EPG_DIR is unset)")

    epg = EpgSource(
        name=payload.name,
        source_kind=payload.source_kind,
        url=payload.url,
        iptv_org_selection=payload.iptv_org_selection.model_dump_json() if payload.iptv_org_selection else None,
        refresh_interval_minutes=payload.refresh_interval_minutes,
    )
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


@router.post("/refresh-all")
async def refresh_all_epg_sources(db: DbSession, _admin: AdminUser) -> dict:
    """Refreshes every EPG source (URL-based and iptv-org). Playlists reflect this immediately
    once it commits - guide data is read live from EpgChannel/EpgProgram, not cached per
    playlist. This does not re-run EPG auto-mapping for newly-added channels or auto-clear;
    use Scheduler's "Sync Now" for the full pass."""
    summary = await sync_all_epg_sources(db)
    await db.commit()
    return summary


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
