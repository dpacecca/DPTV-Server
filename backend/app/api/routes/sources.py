from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DbSession
from app.models.base import ChannelType, SourceType
from app.models.epg import EpgSource
from app.models.source import Source, SourceCategory, SourceChannel
from app.services import epg_refresh_jobs
from app.services.xtream_client import detect_builtin_epg_url
from app.services.sync_engine import sync_all_sources, sync_source

router = APIRouter(prefix="/api/sources", tags=["sources"])


class DetectEpgIn(BaseModel):
    type: SourceType
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    m3u_url: str | None = None


class NewEpgSourceIn(BaseModel):
    """Creates a new EpgSource (same shape as POST /api/epg-sources) and points this source at
    it, in one step - for the "add a new EPG link" choice in the source's EPG picker, including
    when it's pre-filled from a detected builtin URL (see detect_builtin_epg below)."""

    name: str
    url: str
    refresh_interval_minutes: int = 720


class SourceIn(BaseModel):
    name: str
    type: SourceType
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    m3u_url: str | None = None
    m3u_uses_userpass: bool = False
    prefix: str = ""
    suffix: str = ""
    color: str = "#4dabf7"
    ignore_vod: bool = False
    ignore_series: bool = False
    auto_sync_on_start: bool = False
    auto_enable_new_groups: bool = True
    auto_clear_removed_days: int | None = None
    provider_uses_tokens: bool = False
    use_api_for_series: bool = False
    enabled: bool = True
    epg_source_id: int | None = None
    """Which existing EpgSource this source's channels should auto-match against - the "select an
    existing one" choice. Ignored if new_epg_source is also given (that takes precedence and
    overwrites this with the freshly-created EpgSource's id)."""
    new_epg_source: NewEpgSourceIn | None = None
    """The "use the source's default EPG" and "add a new EPG link" choices both land here - the
    only difference between them is what the admin put in its url field (a detected one vs. one
    they typed themselves). None of this, with epg_source_id also left unset, means "no EPG for
    this source" (manual per-channel mapping)."""


class SourceOut(SourceIn):
    id: int
    epg_source_name: str | None = None
    last_sync_at: str | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None

    class Config:
        from_attributes = True


class SourceUpdate(BaseModel):
    """Every field optional, applied with exclude_unset (a field explicitly sent as null DOES
    clear it - e.g. epg_source_id: null drops back to "no EPG" - only an omitted field is left
    alone) - unlike SourceIn's blanket-overwrite use in create_source, this lets the "Edit EPG"
    modal touch just the EPG fields without having to echo back every other setting."""

    name: str | None = None
    type: SourceType | None = None
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    m3u_url: str | None = None
    m3u_uses_userpass: bool | None = None
    prefix: str | None = None
    suffix: str | None = None
    color: str | None = None
    ignore_vod: bool | None = None
    ignore_series: bool | None = None
    auto_sync_on_start: bool | None = None
    auto_enable_new_groups: bool | None = None
    auto_clear_removed_days: int | None = None
    provider_uses_tokens: bool | None = None
    use_api_for_series: bool | None = None
    enabled: bool | None = None
    epg_source_id: int | None = None
    new_epg_source: NewEpgSourceIn | None = None


class CategoryOut(BaseModel):
    id: int
    external_id: str
    name: str
    channel_type: ChannelType
    enabled: bool
    channel_count: int = 0

    class Config:
        from_attributes = True


class ChannelOut(BaseModel):
    id: int
    name: str
    external_stream_id: str
    stream_type: ChannelType
    tvg_id: str | None
    logo_url: str | None
    removed_at: str | None = None

    class Config:
        from_attributes = True


def _serialize_source(source: Source) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "type": source.type,
        "base_url": source.base_url,
        "username": source.username,
        "password": source.password,
        "m3u_url": source.m3u_url,
        "m3u_uses_userpass": source.m3u_uses_userpass,
        "prefix": source.prefix,
        "suffix": source.suffix,
        "color": source.color,
        "ignore_vod": source.ignore_vod,
        "ignore_series": source.ignore_series,
        "auto_sync_on_start": source.auto_sync_on_start,
        "auto_enable_new_groups": source.auto_enable_new_groups,
        "auto_clear_removed_days": source.auto_clear_removed_days,
        "provider_uses_tokens": source.provider_uses_tokens,
        "use_api_for_series": source.use_api_for_series,
        "enabled": source.enabled,
        "epg_source_id": source.epg_source_id,
        "epg_source_name": source.epg_source.name if source.epg_source else None,
        "last_sync_at": source.last_sync_at.isoformat() if source.last_sync_at else None,
        "last_sync_status": source.last_sync_status,
        "last_sync_error": source.last_sync_error,
    }


async def _apply_new_epg_source(db: DbSession, source: Source, new_epg_source: NewEpgSourceIn) -> None:
    """Creates the EpgSource, points `source` at it, and kicks a background refresh so it isn't
    sitting empty until the next scheduled one - shared by create_source and update_source since
    both let "new_epg_source" land on a source the same way."""
    epg = EpgSource(
        name=new_epg_source.name, url=new_epg_source.url, refresh_interval_minutes=new_epg_source.refresh_interval_minutes
    )
    db.add(epg)
    await db.flush()
    source.epg_source_id = epg.id


@router.get("")
async def list_sources(db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(select(Source).options(selectinload(Source.epg_source)).order_by(Source.name))
    return [_serialize_source(s) for s in result.scalars().all()]


@router.post("")
async def create_source(payload: SourceIn, db: DbSession, _admin: AdminUser) -> dict:
    data = payload.model_dump(exclude={"new_epg_source"})
    source = Source(**data)
    db.add(source)
    await db.flush()
    if payload.new_epg_source is not None:
        await _apply_new_epg_source(db, source, payload.new_epg_source)
    await db.commit()
    await db.refresh(source, attribute_names=["epg_source"])
    if payload.new_epg_source is not None:
        epg_refresh_jobs.start_single_refresh(source.epg_source_id)
    return _serialize_source(source)


@router.get("/{source_id}")
async def get_source(source_id: int, db: DbSession, _admin: AdminUser) -> dict:
    source = await db.get(Source, source_id, options=[selectinload(Source.epg_source)])
    if source is None:
        raise HTTPException(404, "Source not found")
    return _serialize_source(source)


@router.post("/detect-epg")
async def detect_epg(payload: DetectEpgIn, _admin: AdminUser) -> dict:
    """Best-effort lookup of a source's own advertised EPG, run against whatever connection
    fields the admin has filled in so far (the source doesn't need to exist yet) - powers the
    "use the source's default EPG" option in the add/edit form. See
    xtream_client.detect_builtin_epg_url for what "best-effort" means here."""
    url = await detect_builtin_epg_url(payload.type, payload.base_url, payload.username, payload.password, payload.m3u_url)
    return {"url": url}


@router.put("/{source_id}")
async def update_source(source_id: int, payload: SourceUpdate, db: DbSession, _admin: AdminUser) -> dict:
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    updates = payload.model_dump(exclude_unset=True, exclude={"new_epg_source"})
    for key, value in updates.items():
        setattr(source, key, value)
    if payload.new_epg_source is not None:
        await _apply_new_epg_source(db, source, payload.new_epg_source)
    await db.commit()
    await db.refresh(source, attribute_names=["epg_source"])
    if payload.new_epg_source is not None:
        epg_refresh_jobs.start_single_refresh(source.epg_source_id)
    return _serialize_source(source)


@router.delete("/{source_id}")
async def delete_source(source_id: int, db: DbSession, _admin: AdminUser) -> dict:
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    await db.delete(source)
    await db.commit()
    return {"ok": True}


@router.post("/sync-all")
async def sync_all(db: DbSession, _admin: AdminUser) -> dict:
    """Syncs every enabled source. Playlists aren't touched directly by this - they're built
    from live queries against Source/SourceChannel, so once this commits, any playlist output
    (M3U/XMLTV/XC API) reflects it immediately. New provider channels still only land in a
    playlist if that source's category is linked for auto-import (New Channel Manager); use
    Scheduler's "Sync Now" instead for that plus EPG auto-mapping/auto-clear in one pass."""
    summary = await sync_all_sources(db)
    await db.commit()
    return summary


@router.post("/{source_id}/sync")
async def trigger_source_sync(source_id: int, db: DbSession, _admin: AdminUser) -> dict:
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    try:
        summary = await sync_source(db, source)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(502, f"Sync failed: {exc}") from exc
    return summary


@router.get("/{source_id}/categories")
async def list_categories(source_id: int, db: DbSession, _admin: AdminUser) -> list[CategoryOut]:
    result = await db.execute(
        select(SourceCategory, func.count(SourceChannel.id))
        .outerjoin(SourceChannel, SourceChannel.source_category_id == SourceCategory.id)
        .where(SourceCategory.source_id == source_id, SourceCategory.removed_at.is_(None))
        .group_by(SourceCategory.id)
        .order_by(SourceCategory.sort_order, SourceCategory.name)
    )
    out = []
    for cat, count in result.all():
        item = CategoryOut.model_validate(cat)
        item.channel_count = count
        out.append(item)
    return out


@router.patch("/categories/{category_id}")
async def update_category(category_id: int, enabled: bool, db: DbSession, _admin: AdminUser) -> CategoryOut:
    cat = await db.get(SourceCategory, category_id)
    if cat is None:
        raise HTTPException(404, "Category not found")
    cat.enabled = enabled
    await db.commit()
    await db.refresh(cat)
    return CategoryOut.model_validate(cat)


@router.get("/categories/{category_id}/channels")
async def list_channels(
    category_id: int,
    db: DbSession,
    _admin: AdminUser,
    q: str | None = None,
    offset: int = 0,
    limit: int = 200,
) -> dict:
    """Paginated/searchable - a source category can hold tens of thousands of channels
    straight from a provider catalog, so this never returns the whole thing at once."""
    limit = max(1, min(limit, 500))
    query = select(SourceChannel).where(SourceChannel.source_category_id == category_id)
    if q:
        query = query.where(SourceChannel.name.ilike(f"%{q}%"))

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    # Provider order when just browsing (matches what actually gets imported); alphabetical only
    # while actively searching, where finding a specific channel by name matters more.
    order = SourceChannel.name if q else (SourceChannel.sort_order, SourceChannel.id)
    order_cols = order if isinstance(order, tuple) else (order,)
    result = await db.execute(query.order_by(*order_cols).offset(offset).limit(limit))

    items = []
    for c in result.scalars().all():
        item = ChannelOut.model_validate(c)
        item.removed_at = c.removed_at.isoformat() if c.removed_at else None
        items.append(item)
    return {"items": items, "total": total, "offset": offset, "limit": limit}
