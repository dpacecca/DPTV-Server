import re

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DbSession
from app.models.base import ChannelType, DummyEpgMode, EpgMatchType
from app.models.epg import EpgChannel
from app.models.playlist import (
    Playlist,
    PlaylistCategory,
    PlaylistCategorySourceLink,
    PlaylistChannel,
)
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.xc_user import XcUser
from app.services import epg_mapper
from app.services.epg_writer import build_xmltv
from app.services.m3u_writer import build_m3u

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _full_load():
    """Eager-loads every category and every channel in one shot.

    Only for paths that genuinely need the whole playlist materialized at once - generating
    the M3U/XMLTV output files. The admin UI never uses this: a playlist can have tens of
    thousands of channels, and shipping/rendering all of them at once is exactly the kind of
    thing that makes a browser-based playlist manager fall over. UI-facing endpoints below use
    SQL COUNT for category summaries and a paginated/searchable endpoint for channel rows.
    """
    return (
        selectinload(Playlist.categories)
        .selectinload(PlaylistCategory.channels)
        .selectinload(PlaylistChannel.source_channel),
        selectinload(Playlist.categories).selectinload(PlaylistCategory.channels).selectinload(PlaylistChannel.epg_channel),
    )


async def _get_playlist_full(db: DbSession, playlist_id: int) -> Playlist:
    result = await db.execute(select(Playlist).where(Playlist.id == playlist_id).options(*_full_load()))
    playlist = result.unique().scalar_one_or_none()
    if playlist is None:
        raise HTTPException(404, "Playlist not found")
    return playlist


def _serialize_channel(pc: PlaylistChannel) -> dict:
    return {
        "id": pc.id,
        "name": pc.name,
        "name_locked": pc.name_locked,
        "number": pc.number,
        "enabled": pc.enabled,
        "sort_order": pc.sort_order,
        "logo_url": pc.logo_url_override or (pc.source_channel.logo_url if pc.source_channel else None),
        "manual_stream_url": pc.manual_stream_url,
        "provider_name": pc.source_channel.name if pc.source_channel else None,
        "source_channel_id": pc.source_channel_id,
        "epg_channel_id": pc.epg_channel_id,
        "epg_display_name": pc.epg_channel.display_name if pc.epg_channel else None,
        "epg_match_type": pc.epg_match_type,
        "dummy_epg_mode": pc.dummy_epg_mode,
        "dummy_epg_program_minutes": pc.dummy_epg_program_minutes,
    }


def _serialize_category_summary(cat: PlaylistCategory, channel_count: int) -> dict:
    """Category shape for the UI: counts only, never the channel rows themselves."""
    return {
        "id": cat.id,
        "name": cat.name,
        "channel_type": cat.channel_type,
        "sort_order": cat.sort_order,
        "dummy_epg_for_unassigned": cat.dummy_epg_for_unassigned,
        "dummy_epg_program_minutes": cat.dummy_epg_program_minutes,
        "channel_count": channel_count,
    }


async def _category_channel_counts(db: DbSession, category_ids: list[int]) -> dict[int, int]:
    if not category_ids:
        return {}
    result = await db.execute(
        select(PlaylistChannel.playlist_category_id, func.count(PlaylistChannel.id))
        .where(PlaylistChannel.playlist_category_id.in_(category_ids))
        .group_by(PlaylistChannel.playlist_category_id)
    )
    return dict(result.all())


def _serialize_playlist_base(pl: Playlist, category_count: int, channel_count: int) -> dict:
    return {
        "id": pl.id,
        "name": pl.name,
        "enabled": pl.enabled,
        "xc_enabled": pl.xc_enabled,
        "m3u_output_enabled": pl.m3u_output_enabled,
        "m3u_filename": pl.m3u_filename,
        "epg_output_enabled": pl.epg_output_enabled,
        "epg_filename": pl.epg_filename,
        "epg_days_to_keep": pl.epg_days_to_keep,
        "category_count": category_count,
        "channel_count": channel_count,
    }


class PlaylistIn(BaseModel):
    name: str
    enabled: bool = True
    xc_enabled: bool = True
    m3u_output_enabled: bool = True
    m3u_filename: str = "playlist.m3u"
    epg_output_enabled: bool = True
    epg_filename: str = "epg.xml"
    epg_days_to_keep: int | None = None


@router.get("")
async def list_playlists(db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(
        select(
            Playlist,
            func.count(func.distinct(PlaylistCategory.id)),
            func.count(PlaylistChannel.id),
        )
        .outerjoin(PlaylistCategory, PlaylistCategory.playlist_id == Playlist.id)
        .outerjoin(PlaylistChannel, PlaylistChannel.playlist_category_id == PlaylistCategory.id)
        .group_by(Playlist.id)
        .order_by(Playlist.name)
    )
    return [_serialize_playlist_base(pl, cat_count, chan_count) for pl, cat_count, chan_count in result.all()]


async def _get_playlist_with_category_summaries(db: DbSession, playlist_id: int) -> dict:
    pl = await db.get(Playlist, playlist_id)
    if pl is None:
        raise HTTPException(404, "Playlist not found")
    cats_result = await db.execute(
        select(PlaylistCategory)
        .where(PlaylistCategory.playlist_id == playlist_id)
        .order_by(PlaylistCategory.sort_order)
    )
    categories = cats_result.scalars().all()
    counts = await _category_channel_counts(db, [c.id for c in categories])
    base = _serialize_playlist_base(pl, len(categories), sum(counts.values()))
    base["categories"] = [_serialize_category_summary(c, counts.get(c.id, 0)) for c in categories]
    return base


@router.post("")
async def create_playlist(payload: PlaylistIn, db: DbSession, _admin: AdminUser) -> dict:
    pl = Playlist(**payload.model_dump())
    db.add(pl)
    await db.commit()
    return await _get_playlist_with_category_summaries(db, pl.id)


@router.get("/{playlist_id}")
async def get_playlist(playlist_id: int, db: DbSession, _admin: AdminUser) -> dict:
    return await _get_playlist_with_category_summaries(db, playlist_id)


@router.put("/{playlist_id}")
async def update_playlist(playlist_id: int, payload: PlaylistIn, db: DbSession, _admin: AdminUser) -> dict:
    pl = await db.get(Playlist, playlist_id)
    if pl is None:
        raise HTTPException(404, "Playlist not found")
    for key, value in payload.model_dump().items():
        setattr(pl, key, value)
    await db.commit()
    return await _get_playlist_with_category_summaries(db, playlist_id)


@router.delete("/{playlist_id}")
async def delete_playlist(playlist_id: int, db: DbSession, _admin: AdminUser) -> dict:
    pl = await db.get(Playlist, playlist_id)
    if pl is None:
        raise HTTPException(404, "Playlist not found")
    await db.delete(pl)
    await db.commit()
    return {"ok": True}


# ---------- Categories ----------


class CategoryIn(BaseModel):
    name: str
    channel_type: ChannelType = ChannelType.LIVE
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None
    dummy_epg_for_unassigned: bool | None = None
    dummy_epg_program_minutes: int | None = None


@router.post("/{playlist_id}/categories")
async def create_category(playlist_id: int, payload: CategoryIn, db: DbSession, _admin: AdminUser) -> dict:
    if await db.get(Playlist, playlist_id) is None:
        raise HTTPException(404, "Playlist not found")
    cat = PlaylistCategory(playlist_id=playlist_id, **payload.model_dump())
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return _serialize_category_summary(cat, channel_count=0)


@router.patch("/{playlist_id}/categories/{category_id}")
async def update_category(
    playlist_id: int, category_id: int, payload: CategoryUpdate, db: DbSession, _admin: AdminUser
) -> dict:
    cat = await db.get(PlaylistCategory, category_id)
    if cat is None or cat.playlist_id != playlist_id:
        raise HTTPException(404, "Category not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(cat, key, value)
    await db.commit()
    await db.refresh(cat)
    counts = await _category_channel_counts(db, [cat.id])
    return _serialize_category_summary(cat, channel_count=counts.get(cat.id, 0))


@router.delete("/{playlist_id}/categories/{category_id}")
async def delete_category(playlist_id: int, category_id: int, db: DbSession, _admin: AdminUser) -> dict:
    cat = await db.get(PlaylistCategory, category_id)
    if cat is None or cat.playlist_id != playlist_id:
        raise HTTPException(404, "Category not found")
    await db.delete(cat)
    await db.commit()
    return {"ok": True}


class ReorderItem(BaseModel):
    id: int
    sort_order: int


@router.post("/{playlist_id}/categories/reorder")
async def reorder_categories(playlist_id: int, items: list[ReorderItem], db: DbSession, _admin: AdminUser) -> dict:
    for item in items:
        cat = await db.get(PlaylistCategory, item.id)
        if cat and cat.playlist_id == playlist_id:
            cat.sort_order = item.sort_order
    await db.commit()
    return {"ok": True}


class MergeIn(BaseModel):
    from_category_ids: list[int]


@router.post("/{playlist_id}/categories/{category_id}/merge")
async def merge_categories(
    playlist_id: int, category_id: int, payload: MergeIn, db: DbSession, _admin: AdminUser
) -> dict:
    target = await db.get(PlaylistCategory, category_id)
    if target is None or target.playlist_id != playlist_id:
        raise HTTPException(404, "Target category not found")
    for src_id in payload.from_category_ids:
        if src_id == category_id:
            continue
        result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.playlist_category_id == src_id))
        for pc in result.scalars().all():
            pc.playlist_category_id = category_id
        src_cat = await db.get(PlaylistCategory, src_id)
        if src_cat and src_cat.playlist_id == playlist_id:
            await db.delete(src_cat)
    await db.commit()
    return {"ok": True}


# ---------- New Channel Manager: source links ----------


class SourceLinkIn(BaseModel):
    source_category_id: int


@router.get("/{playlist_id}/categories/{category_id}/source-links")
async def list_source_links(playlist_id: int, category_id: int, db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(
        select(PlaylistCategorySourceLink, SourceCategory)
        .join(SourceCategory, PlaylistCategorySourceLink.source_category_id == SourceCategory.id)
        .where(PlaylistCategorySourceLink.playlist_category_id == category_id)
    )
    return [
        {"link_id": link.id, "source_category_id": sc.id, "source_category_name": sc.name}
        for link, sc in result.all()
    ]


@router.post("/{playlist_id}/categories/{category_id}/source-links")
async def add_source_link(
    playlist_id: int, category_id: int, payload: SourceLinkIn, db: DbSession, _admin: AdminUser
) -> dict:
    link = PlaylistCategorySourceLink(playlist_category_id=category_id, source_category_id=payload.source_category_id)
    db.add(link)
    await db.commit()
    return {"ok": True, "id": link.id}


@router.delete("/{playlist_id}/categories/{category_id}/source-links/{link_id}")
async def remove_source_link(playlist_id: int, category_id: int, link_id: int, db: DbSession, _admin: AdminUser) -> dict:
    link = await db.get(PlaylistCategorySourceLink, link_id)
    if link:
        await db.delete(link)
        await db.commit()
    return {"ok": True}


# ---------- Import from sources ----------


class ImportIn(BaseModel):
    source_id: int
    channel_type: ChannelType = ChannelType.LIVE
    category_ids: list[int] | None = None
    """Import all channels of these SourceCategory ids."""
    channel_ids: list[int] | None = None
    """Import these specific SourceChannel ids."""
    target_category_id: int | None = None
    target_category_name: str | None = None
    link_for_new_channels: bool = True
    skip_duplicates: bool = True


@router.post("/{playlist_id}/import")
async def import_channels(playlist_id: int, payload: ImportIn, db: DbSession, _admin: AdminUser) -> dict:
    if await db.get(Playlist, playlist_id) is None:
        raise HTTPException(404, "Playlist not found")

    if payload.target_category_id:
        target_cat = await db.get(PlaylistCategory, payload.target_category_id)
        if target_cat is None or target_cat.playlist_id != playlist_id:
            raise HTTPException(404, "Target category not found")
    elif payload.target_category_name:
        result = await db.execute(
            select(PlaylistCategory).where(
                PlaylistCategory.playlist_id == playlist_id,
                PlaylistCategory.name == payload.target_category_name,
            )
        )
        target_cat = result.scalar_one_or_none()
        if target_cat is None:
            target_cat = PlaylistCategory(
                playlist_id=playlist_id, name=payload.target_category_name, channel_type=payload.channel_type
            )
            db.add(target_cat)
            await db.flush()
    else:
        raise HTTPException(400, "target_category_id or target_category_name is required")

    source_category_ids = set(payload.category_ids or [])
    channel_query = select(SourceChannel).where(SourceChannel.removed_at.is_(None))
    if payload.channel_ids:
        channel_query = channel_query.where(SourceChannel.id.in_(payload.channel_ids))
    elif source_category_ids:
        channel_query = channel_query.where(SourceChannel.source_category_id.in_(source_category_ids))
    else:
        cats_result = await db.execute(
            select(SourceCategory.id).where(
                SourceCategory.source_id == payload.source_id, SourceCategory.enabled.is_(True)
            )
        )
        source_category_ids = {row[0] for row in cats_result.all()}
        channel_query = channel_query.where(SourceChannel.source_category_id.in_(source_category_ids))

    channels_result = await db.execute(channel_query)
    source_channels = channels_result.scalars().all()

    existing_source_channel_ids: set[int] = set()
    if payload.skip_duplicates:
        existing_result = await db.execute(
            select(PlaylistChannel.source_channel_id).where(
                PlaylistChannel.playlist_category_id == target_cat.id,
                PlaylistChannel.source_channel_id.is_not(None),
            )
        )
        existing_source_channel_ids = {row[0] for row in existing_result.all()}

    imported = 0
    involved_source_category_ids = set()
    for sc in source_channels:
        involved_source_category_ids.add(sc.source_category_id)
        if payload.skip_duplicates and sc.id in existing_source_channel_ids:
            continue
        db.add(
            PlaylistChannel(
                playlist_category_id=target_cat.id,
                source_channel_id=sc.id,
                name=sc.name,
                enabled=True,
            )
        )
        imported += 1

    if payload.link_for_new_channels:
        linked_result = await db.execute(
            select(PlaylistCategorySourceLink.source_category_id).where(
                PlaylistCategorySourceLink.playlist_category_id == target_cat.id
            )
        )
        already_linked = {row[0] for row in linked_result.all()}
        for source_cat_id in involved_source_category_ids - already_linked:
            db.add(
                PlaylistCategorySourceLink(playlist_category_id=target_cat.id, source_category_id=source_cat_id)
            )

    await db.commit()
    return {"imported": imported, "target_category_id": target_cat.id}


MAX_PAGE_SIZE = 500


def _category_channels_query(category_id: int, q: str | None, enabled: bool | None):
    query = select(PlaylistChannel).where(PlaylistChannel.playlist_category_id == category_id)
    if q:
        query = query.where(PlaylistChannel.name.ilike(f"%{q}%"))
    if enabled is not None:
        query = query.where(PlaylistChannel.enabled == enabled)
    return query


@router.get("/{playlist_id}/categories/{category_id}/channels")
async def list_category_channels(
    playlist_id: int,
    category_id: int,
    db: DbSession,
    _admin: AdminUser,
    q: str | None = None,
    enabled: bool | None = None,
    offset: int = 0,
    limit: int = 200,
) -> dict:
    """Paginated, searchable channel listing for one category.

    A playlist category can hold tens of thousands of channels (a straight dump from a large
    provider catalog), so the admin UI never asks for "all of them" - it pages through this
    endpoint and virtualizes the rows client-side. `channels/ids` below exists for bulk actions
    that need every id matching the current filter without paying to serialize every row.
    """
    cat = await db.get(PlaylistCategory, category_id)
    if cat is None or cat.playlist_id != playlist_id:
        raise HTTPException(404, "Category not found")

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    base_query = _category_channels_query(category_id, q, enabled)

    total = await db.scalar(select(func.count()).select_from(base_query.subquery()))
    result = await db.execute(
        base_query.options(selectinload(PlaylistChannel.source_channel), selectinload(PlaylistChannel.epg_channel))
        .order_by(PlaylistChannel.sort_order, PlaylistChannel.id)
        .offset(offset)
        .limit(limit)
    )
    items = [_serialize_channel(pc) for pc in result.scalars().all()]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/{playlist_id}/categories/{category_id}/channels/ids")
async def list_category_channel_ids(
    playlist_id: int,
    category_id: int,
    db: DbSession,
    _admin: AdminUser,
    q: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """All channel ids matching the current filter - cheap even at 100k+ rows since it's just
    integers. Backs "select all matching" in the UI without ever materializing full rows."""
    cat = await db.get(PlaylistCategory, category_id)
    if cat is None or cat.playlist_id != playlist_id:
        raise HTTPException(404, "Category not found")
    result = await db.execute(_category_channels_query(category_id, q, enabled).with_only_columns(PlaylistChannel.id))
    return {"ids": [row[0] for row in result.all()]}


class ManualChannelIn(BaseModel):
    name: str
    stream_url: str | None = None
    enabled: bool = True


@router.post("/{playlist_id}/categories/{category_id}/channels")
async def add_manual_channel(
    playlist_id: int, category_id: int, payload: ManualChannelIn, db: DbSession, _admin: AdminUser
) -> dict:
    cat = await db.get(PlaylistCategory, category_id)
    if cat is None or cat.playlist_id != playlist_id:
        raise HTTPException(404, "Category not found")
    pc = PlaylistChannel(
        playlist_category_id=category_id,
        name=payload.name,
        manual_stream_url=payload.stream_url,
        enabled=payload.enabled,
    )
    db.add(pc)
    await db.commit()
    await db.refresh(pc, attribute_names=["source_channel", "epg_channel"])
    return _serialize_channel(pc)


# ---------- Channel move / copy / edit / bulk ----------


class ChannelBatchTarget(BaseModel):
    channel_ids: list[int]
    target_category_id: int


@router.post("/{playlist_id}/channels/move")
async def move_channels(playlist_id: int, payload: ChannelBatchTarget, db: DbSession, _admin: AdminUser) -> dict:
    target_cat = await db.get(PlaylistCategory, payload.target_category_id)
    if target_cat is None or target_cat.playlist_id != playlist_id:
        raise HTTPException(404, "Target category not found")
    result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.id.in_(payload.channel_ids)))
    moved = 0
    for pc in result.scalars().all():
        pc.playlist_category_id = payload.target_category_id
        moved += 1
    await db.commit()
    return {"moved": moved}


@router.post("/{playlist_id}/channels/copy")
async def copy_channels(playlist_id: int, payload: ChannelBatchTarget, db: DbSession, _admin: AdminUser) -> dict:
    target_cat = await db.get(PlaylistCategory, payload.target_category_id)
    if target_cat is None or target_cat.playlist_id != playlist_id:
        raise HTTPException(404, "Target category not found")
    result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.id.in_(payload.channel_ids)))
    copied = 0
    for pc in result.scalars().all():
        db.add(
            PlaylistChannel(
                playlist_category_id=payload.target_category_id,
                source_channel_id=pc.source_channel_id,
                name=pc.name,
                manual_stream_url=pc.manual_stream_url,
                name_locked=pc.name_locked,
                number=pc.number,
                logo_url_override=pc.logo_url_override,
                enabled=pc.enabled,
                epg_channel_id=pc.epg_channel_id,
                epg_match_type=pc.epg_match_type,
                dummy_epg_mode=pc.dummy_epg_mode,
                dummy_epg_program_minutes=pc.dummy_epg_program_minutes,
            )
        )
        copied += 1
    await db.commit()
    return {"copied": copied}


class ChannelUpdate(BaseModel):
    name: str | None = None
    name_locked: bool | None = None
    number: int | None = None
    enabled: bool | None = None
    logo_url_override: str | None = None
    manual_stream_url: str | None = None
    dummy_epg_mode: DummyEpgMode | None = None
    dummy_epg_program_minutes: int | None = None


@router.patch("/{playlist_id}/channels/{channel_id}")
async def update_channel(playlist_id: int, channel_id: int, payload: ChannelUpdate, db: DbSession, _admin: AdminUser) -> dict:
    pc = await db.get(PlaylistChannel, channel_id, options=[selectinload(PlaylistChannel.source_channel), selectinload(PlaylistChannel.epg_channel)])
    if pc is None:
        raise HTTPException(404, "Channel not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(pc, key, value)
    await db.commit()
    await db.refresh(pc, attribute_names=["source_channel", "epg_channel"])
    return _serialize_channel(pc)


@router.post("/{playlist_id}/channels/{channel_id}/revert-name")
async def revert_channel_name(playlist_id: int, channel_id: int, db: DbSession, _admin: AdminUser) -> dict:
    pc = await db.get(PlaylistChannel, channel_id, options=[selectinload(PlaylistChannel.source_channel), selectinload(PlaylistChannel.epg_channel)])
    if pc is None:
        raise HTTPException(404, "Channel not found")
    if pc.source_channel:
        pc.name = pc.source_channel.name
    await db.commit()
    return _serialize_channel(pc)


@router.delete("/{playlist_id}/channels/{channel_id}")
async def delete_channel(playlist_id: int, channel_id: int, db: DbSession, _admin: AdminUser) -> dict:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    await db.delete(pc)
    await db.commit()
    return {"ok": True}


class BulkAction(BaseModel):
    channel_ids: list[int]
    action: str
    """One of: uppercase, sentence_case, add_prefix, add_suffix, find_replace, enable, disable, delete, lock_name, unlock_name."""
    find: str | None = None
    replace: str | None = None
    text: str | None = None


@router.post("/{playlist_id}/channels/bulk")
async def bulk_edit_channels(playlist_id: int, payload: BulkAction, db: DbSession, _admin: AdminUser) -> dict:
    result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.id.in_(payload.channel_ids)))
    channels = result.scalars().all()
    count = 0
    for pc in channels:
        if payload.action == "uppercase":
            pc.name = pc.name.upper()
        elif payload.action == "sentence_case":
            pc.name = pc.name.capitalize()
        elif payload.action == "add_prefix":
            pc.name = f"{payload.text or ''}{pc.name}"
        elif payload.action == "add_suffix":
            pc.name = f"{pc.name}{payload.text or ''}"
        elif payload.action == "find_replace":
            pc.name = re.sub(re.escape(payload.find or ""), payload.replace or "", pc.name)
        elif payload.action == "enable":
            pc.enabled = True
        elif payload.action == "disable":
            pc.enabled = False
        elif payload.action == "lock_name":
            pc.name_locked = True
        elif payload.action == "unlock_name":
            pc.name_locked = False
        elif payload.action == "delete":
            await db.delete(pc)
        else:
            raise HTTPException(400, f"Unknown action: {payload.action}")
        count += 1
    await db.commit()
    return {"affected": count}


# ---------- EPG mapping ----------


@router.get("/{playlist_id}/channels/{channel_id}/epg/search")
async def search_epg(
    playlist_id: int, channel_id: int, db: DbSession, _admin: AdminUser, q: str | None = None, limit: int = 10
) -> list[dict]:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    query_name = q or pc.name
    result = await db.execute(select(EpgChannel))
    all_channels = result.scalars().all()
    matches = epg_mapper.search_candidates(query_name, all_channels, limit=limit)
    return [
        {"epg_channel_id": ch.id, "display_name": ch.display_name, "epg_id": ch.epg_channel_id, "score": score}
        for ch, score in matches
    ]


@router.post("/{playlist_id}/channels/{channel_id}/epg/auto")
async def auto_map_epg(
    playlist_id: int, channel_id: int, db: DbSession, _admin: AdminUser, sensitivity: float = 0.9
) -> dict:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    result = await db.execute(select(EpgChannel))
    all_channels = result.scalars().all()
    best = epg_mapper.auto_match(pc.name, all_channels, sensitivity=sensitivity)
    if best is None:
        return {"matched": False}
    pc.epg_channel_id = best.id
    pc.epg_match_type = EpgMatchType.AUTO
    await db.commit()
    return {"matched": True, "epg_channel_id": best.id, "display_name": best.display_name}


class EpgAssign(BaseModel):
    epg_channel_id: int | None


@router.patch("/{playlist_id}/channels/{channel_id}/epg")
async def assign_epg(playlist_id: int, channel_id: int, payload: EpgAssign, db: DbSession, _admin: AdminUser) -> dict:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    pc.epg_channel_id = payload.epg_channel_id
    pc.epg_match_type = EpgMatchType.MANUAL if payload.epg_channel_id else EpgMatchType.NONE
    await db.commit()
    return {"ok": True}


# ---------- Output ----------


@router.get("/{playlist_id}/output/m3u")
async def output_m3u(playlist_id: int, xc_user_id: int, db: DbSession, _admin: AdminUser) -> Response:
    playlist = await _get_playlist_full(db, playlist_id)
    xc_user = await db.get(XcUser, xc_user_id)
    if xc_user is None:
        raise HTTPException(404, "XC user not found")
    text = build_m3u(playlist, xc_user)
    return Response(content=text, media_type="application/x-mpegurl")


@router.get("/{playlist_id}/output/epg.xml")
async def output_epg(playlist_id: int, db: DbSession, _admin: AdminUser) -> Response:
    playlist = await _get_playlist_full(db, playlist_id)
    xml_bytes = await build_xmltv(db, playlist)
    return Response(content=xml_bytes, media_type="application/xml")
