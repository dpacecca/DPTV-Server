import re

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DbSession
from app.config import get_settings
from app.models.base import ChannelType, DummyEpgMode, EpgMatchType, SourceType
from app.models.epg import EpgChannel
from app.models.playlist import (
    DummyEpgRule,
    Playlist,
    PlaylistCategory,
    PlaylistCategorySourceLink,
    PlaylistChannel,
)
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.xc_user import XcUser
from app.services import duplicate_scanner, dummy_epg, epg_mapper, scan_jobs
from app.services.epg_writer import build_xmltv
from app.services.m3u_parser import parse_m3u
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


_STREAM_ID_RE = re.compile(r"/(?:live|movie|series)/[^/]+/[^/]+/([^/.]+)(?:\.[a-zA-Z0-9]+)?/?$")


def _extract_stream_id(url: str) -> str | None:
    """Pulls the stream id out of an Xtream-style pass-through URL
    (".../live/{user}/{pass}/{id}.{ext}"), so a channel can still be matched to its source
    channel even if the id's own extension differs from what's on record (e.g. a re-exported
    playlist normalized every URL to .m3u8)."""
    m = _STREAM_ID_RE.search(url)
    return m.group(1) if m else None


@router.post("/import-m3u")
async def import_m3u_playlist(
    db: DbSession,
    _admin: AdminUser,
    file: UploadFile = File(...),
    source_id: int = Form(...),
    playlist_name: str = Form(...),
) -> dict:
    """Creates a new playlist from an uploaded M3U file (e.g. exported from another tool like
    IPTVBoss), matching each entry back to a channel already synced from `source_id` by its
    stream URL - so the imported playlist keeps working through this source (re-sync aware,
    survives the provider rotating credentials) instead of hardcoding the URLs the file shipped
    with. A channel that can't be matched still imports fine, just using its own URL directly."""
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    entries = parse_m3u(text)
    if not entries:
        raise HTTPException(400, "No channels found in this M3U file")

    result = await db.execute(
        select(SourceChannel)
        .join(SourceCategory, SourceChannel.source_category_id == SourceCategory.id)
        .where(SourceCategory.source_id == source_id)
    )
    source_channels = result.scalars().all()
    by_url = {sc.stream_url: sc for sc in source_channels if sc.stream_url}
    by_stream_id = (
        {(sc.stream_type, sc.external_stream_id): sc for sc in source_channels}
        if source.type == SourceType.XTREAM
        else {}
    )

    playlist = Playlist(name=playlist_name)
    db.add(playlist)
    await db.flush()

    categories: dict[tuple[str, ChannelType], PlaylistCategory] = {}
    matched = 0
    unmatched_names: list[str] = []

    for i, entry in enumerate(entries):
        group = entry.group_title or "Uncategorized"
        key = (group, entry.channel_type)
        cat = categories.get(key)
        if cat is None:
            cat = PlaylistCategory(
                playlist_id=playlist.id, name=group, channel_type=entry.channel_type, sort_order=len(categories)
            )
            db.add(cat)
            await db.flush()
            categories[key] = cat

        matched_channel = by_url.get(entry.url)
        if matched_channel is None:
            stream_id = _extract_stream_id(entry.url)
            if stream_id:
                matched_channel = by_stream_id.get((entry.channel_type, stream_id))

        db.add(
            PlaylistChannel(
                playlist_category_id=cat.id,
                source_channel_id=matched_channel.id if matched_channel else None,
                name=entry.name,
                manual_stream_url=None if matched_channel else entry.url,
                sort_order=i,
            )
        )
        if matched_channel:
            matched += 1
        else:
            unmatched_names.append(entry.name)

    await db.commit()
    return {
        "playlist_id": playlist.id,
        "categories": len(categories),
        "channels": len(entries),
        "matched": matched,
        "unmatched": len(unmatched_names),
        "unmatched_names": unmatched_names[:50],
    }


@router.get("/timezones")
async def list_timezones(_admin: AdminUser) -> list[str]:
    """For the dummy EPG rule editor's timezone dropdown. A literal path segment registered
    ahead of GET /{playlist_id} below - that route would otherwise greedily match "timezones"
    as a (non-numeric, 422-failing) playlist_id."""
    return dummy_epg.list_timezones()


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
    mode: str = "merge"
    """'merge' (default): all imported channels land in one target category, picked below.
    'per_category': each selected source category becomes/reuses its own playlist category,
    with the provider's own name and relative order preserved - target_category_id/name are
    ignored in this mode. Requires category_ids."""
    category_ids: list[int] | None = None
    """Import all channels of these SourceCategory ids."""
    channel_ids: list[int] | None = None
    """Import these specific SourceChannel ids. Only valid with mode='merge'."""
    target_category_id: int | None = None
    target_category_name: str | None = None
    link_for_new_channels: bool = True
    skip_duplicates: bool = True


async def _import_source_channels_into(
    db: DbSession,
    target_cat: PlaylistCategory,
    source_channels: list[SourceChannel],
    skip_duplicates: bool,
    link_for_new_channels: bool,
) -> int:
    existing_source_channel_ids: set[int] = set()
    if skip_duplicates:
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
        if skip_duplicates and sc.id in existing_source_channel_ids:
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

    if link_for_new_channels:
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

    return imported


@router.post("/{playlist_id}/import")
async def import_channels(playlist_id: int, payload: ImportIn, db: DbSession, _admin: AdminUser) -> dict:
    if await db.get(Playlist, playlist_id) is None:
        raise HTTPException(404, "Playlist not found")

    if payload.mode == "per_category":
        if not payload.category_ids:
            raise HTTPException(400, "category_ids is required for mode='per_category'")

        cats_result = await db.execute(
            select(SourceCategory)
            .where(SourceCategory.id.in_(payload.category_ids))
            .order_by(SourceCategory.sort_order, SourceCategory.name)
        )
        source_cats = cats_result.scalars().all()

        existing_result = await db.execute(
            select(PlaylistCategory).where(PlaylistCategory.playlist_id == playlist_id)
        )
        existing_by_name_type = {(c.name, c.channel_type): c for c in existing_result.scalars().all()}
        next_sort_order = (max((c.sort_order for c in existing_by_name_type.values()), default=-1)) + 1

        results = []
        total_imported = 0
        for source_cat in source_cats:
            key = (source_cat.name, source_cat.channel_type)
            target_cat = existing_by_name_type.get(key)
            created = target_cat is None
            if target_cat is None:
                target_cat = PlaylistCategory(
                    playlist_id=playlist_id,
                    name=source_cat.name,
                    channel_type=source_cat.channel_type,
                    sort_order=next_sort_order,
                )
                db.add(target_cat)
                await db.flush()
                existing_by_name_type[key] = target_cat
                next_sort_order += 1

            channels_result = await db.execute(
                select(SourceChannel).where(
                    SourceChannel.source_category_id == source_cat.id, SourceChannel.removed_at.is_(None)
                )
            )
            imported = await _import_source_channels_into(
                db, target_cat, channels_result.scalars().all(), payload.skip_duplicates, payload.link_for_new_channels
            )
            total_imported += imported
            results.append(
                {
                    "source_category_id": source_cat.id,
                    "target_category_id": target_cat.id,
                    "target_category_name": target_cat.name,
                    "created": created,
                    "imported": imported,
                }
            )

        await db.commit()
        return {"imported": total_imported, "categories": results}

    # mode == "merge"
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
    imported = await _import_source_channels_into(
        db, target_cat, channels_result.scalars().all(), payload.skip_duplicates, payload.link_for_new_channels
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


# ---------- Quality scan / duplicate detection ----------


class ScanDuplicatesIn(BaseModel):
    channel_ids: list[int] | None = None
    """Scan only these channels. Omit to scan every channel currently in the category."""
    concurrency: int | None = None
    timeout_seconds: float | None = None


@router.post("/{playlist_id}/categories/{category_id}/scan-duplicates")
async def scan_duplicates(
    playlist_id: int, category_id: int, payload: ScanDuplicatesIn, db: DbSession, _admin: AdminUser
) -> dict:
    """Kicks off a background quality scan (ffprobe against each channel's stream URL) of a
    category's channels. Probing dozens of live streams over the network is far too slow for
    one request/response cycle, so this returns a job id immediately and the UI polls
    GET .../scan-jobs/{job_id} for progress and, once done, the duplicate groups found."""
    cat = await db.get(PlaylistCategory, category_id)
    if cat is None or cat.playlist_id != playlist_id:
        raise HTTPException(404, "Category not found")

    query = select(PlaylistChannel.id).where(PlaylistChannel.playlist_category_id == category_id)
    if payload.channel_ids:
        query = query.where(PlaylistChannel.id.in_(payload.channel_ids))
    result = await db.execute(query)
    channel_ids = [row[0] for row in result.all()]
    if not channel_ids:
        raise HTTPException(400, "No channels to scan")

    settings = get_settings()
    concurrency = payload.concurrency or settings.scan_default_concurrency
    concurrency = max(1, min(concurrency, settings.scan_max_concurrency))
    job = scan_jobs.start_scan_job(
        playlist_id,
        category_id,
        channel_ids,
        concurrency=concurrency,
        timeout_seconds=payload.timeout_seconds or settings.scan_default_timeout_seconds,
    )
    return {"job_id": job.id, "total": job.total}


@router.get("/{playlist_id}/scan-jobs/{job_id}")
async def get_scan_job(playlist_id: int, job_id: str, _admin: AdminUser) -> dict:
    job = scan_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "total": job.total,
        "completed": job.completed,
        "error": job.error,
        "results": job.results,
        "duplicate_groups": job.duplicate_groups,
    }


class DedupeGroupIn(BaseModel):
    keep_channel_id: int
    remove_channel_ids: list[int]


class ApplyDedupeIn(BaseModel):
    groups: list[DedupeGroupIn]


@router.post("/{playlist_id}/channels/dedupe/apply")
async def apply_dedupe(playlist_id: int, payload: ApplyDedupeIn, db: DbSession, _admin: AdminUser) -> dict:
    """Deletes the losing channels from each duplicate group. The frontend sends the exact
    keep/remove split (defaulted from the scan's ranking but editable by the admin before
    applying), so this doesn't re-derive "best" itself - it just enforces that a group's keeper
    is never deleted even if the two lists overlap."""
    keep_ids = {g.keep_channel_id for g in payload.groups}
    remove_ids = {cid for g in payload.groups for cid in g.remove_channel_ids} - keep_ids
    if not remove_ids:
        return {"removed": 0}
    result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.id.in_(remove_ids)))
    channels = result.scalars().all()
    for pc in channels:
        await db.delete(pc)
    await db.commit()
    return {"removed": len(channels)}


class TagResolutionIn(BaseModel):
    channel_ids: list[int]


@router.post("/{playlist_id}/channels/tag-resolution")
async def tag_resolution(playlist_id: int, payload: TagResolutionIn, db: DbSession, _admin: AdminUser) -> dict:
    """Appends the last-detected resolution (e.g. "CNN [1080p]") to each channel's name, from
    its most recent scan-duplicates probe. Requires a prior successful scan - channels that were
    never probed, or whose probe didn't detect a resolution, are skipped rather than guessed at.
    Re-tagging after a later re-scan replaces the old tag instead of stacking a new one on."""
    result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.id.in_(payload.channel_ids)))
    channels = result.scalars().all()

    tagged: list[dict] = []
    skipped_locked: list[int] = []
    skipped_not_scanned: list[int] = []
    for pc in channels:
        if pc.name_locked:
            skipped_locked.append(pc.id)
            continue
        label = duplicate_scanner.resolution_label(pc.detected_height)
        if label is None:
            skipped_not_scanned.append(pc.id)
            continue
        base_name = duplicate_scanner.TRAILING_QUALITY_TAG.sub("", pc.name)
        pc.name = f"{base_name} [{label}]"
        tagged.append({"channel_id": pc.id, "name": pc.name})

    await db.commit()
    return {"tagged": tagged, "skipped_locked": skipped_locked, "skipped_not_scanned": skipped_not_scanned}


# ---------- EPG mapping ----------


async def _epg_candidates(db: DbSession, epg_source_ids: list[int] | None) -> list[EpgChannel]:
    """All EPG channels to search against - scoped to selected guides when given, matching
    IPTVBoss's 'Search Options' (e.g. disabling UK/CA guides when mapping US channels so
    similarly-named channels from the wrong region don't win the fuzzy match)."""
    query = select(EpgChannel)
    if epg_source_ids:
        query = query.where(EpgChannel.epg_source_id.in_(epg_source_ids))
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{playlist_id}/channels/{channel_id}/epg/search")
async def search_epg(
    playlist_id: int,
    channel_id: int,
    db: DbSession,
    _admin: AdminUser,
    q: str | None = None,
    limit: int = 10,
    epg_source_ids: list[int] | None = Query(None),
) -> list[dict]:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    query_name = q or pc.name
    candidates = await _epg_candidates(db, epg_source_ids)
    matches = epg_mapper.search_candidates(query_name, candidates, limit=limit)
    return [
        {"epg_channel_id": ch.id, "display_name": ch.display_name, "epg_id": ch.epg_channel_id, "score": score}
        for ch, score in matches
    ]


@router.post("/{playlist_id}/channels/{channel_id}/epg/auto")
async def auto_map_epg(
    playlist_id: int,
    channel_id: int,
    db: DbSession,
    _admin: AdminUser,
    sensitivity: float = 0.9,
    epg_source_ids: list[int] | None = Query(None),
) -> dict:
    pc = await db.get(PlaylistChannel, channel_id)
    if pc is None:
        raise HTTPException(404, "Channel not found")
    candidates = await _epg_candidates(db, epg_source_ids)
    best = epg_mapper.auto_match(pc.name, candidates, sensitivity=sensitivity)
    if best is None:
        return {"matched": False}
    pc.epg_channel_id = best.id
    pc.epg_match_type = EpgMatchType.AUTO
    await db.commit()
    return {"matched": True, "epg_channel_id": best.id, "display_name": best.display_name}


class BulkEpgAutoMapIn(BaseModel):
    channel_ids: list[int]
    sensitivity: float = 0.9
    epg_source_ids: list[int] | None = None
    """Restrict the search to these EPG sources. Omit/empty to search all of them."""


@router.post("/{playlist_id}/channels/epg/bulk-auto-map")
async def bulk_auto_map_epg(playlist_id: int, payload: BulkEpgAutoMapIn, db: DbSession, _admin: AdminUser) -> dict:
    """Auto-map EPG for many channels at once, e.g. everything selected in the channel list."""
    candidates = await _epg_candidates(db, payload.epg_source_ids)
    result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.id.in_(payload.channel_ids)))
    channels = result.scalars().all()

    matched: list[dict] = []
    unmatched: list[dict] = []
    for pc in channels:
        best = epg_mapper.auto_match(pc.name, candidates, sensitivity=payload.sensitivity)
        if best is None:
            unmatched.append({"channel_id": pc.id, "channel_name": pc.name})
            continue
        pc.epg_channel_id = best.id
        pc.epg_match_type = EpgMatchType.AUTO
        matched.append({"channel_id": pc.id, "channel_name": pc.name, "epg_channel_id": best.id, "display_name": best.display_name})

    await db.commit()
    return {"matched": matched, "unmatched": unmatched}


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


# ---------- Dummy EPG rules (advanced "event" mode parsing) ----------


def _serialize_dummy_epg_rule(rule: DummyEpgRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "pattern": rule.pattern,
        "timezone": rule.timezone,
        "enabled": rule.enabled,
        "sort_order": rule.sort_order,
    }


def _validate_pattern_or_400(pattern: str) -> None:
    try:
        dummy_epg.validate_rule_pattern(pattern)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class DummyEpgRuleIn(BaseModel):
    name: str
    pattern: str
    timezone: str | None = None
    """IANA zone (e.g. "America/New_York") the pattern's hour/minute is expressed in. None = UTC."""
    enabled: bool = True


@router.get("/{playlist_id}/dummy-epg-rules")
async def list_dummy_epg_rules(playlist_id: int, db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(
        select(DummyEpgRule).where(DummyEpgRule.playlist_id == playlist_id).order_by(DummyEpgRule.sort_order)
    )
    return [_serialize_dummy_epg_rule(r) for r in result.scalars().all()]


@router.post("/{playlist_id}/dummy-epg-rules")
async def create_dummy_epg_rule(playlist_id: int, payload: DummyEpgRuleIn, db: DbSession, _admin: AdminUser) -> dict:
    _validate_pattern_or_400(payload.pattern)
    count = await db.scalar(
        select(func.count()).select_from(DummyEpgRule).where(DummyEpgRule.playlist_id == playlist_id)
    )
    rule = DummyEpgRule(playlist_id=playlist_id, sort_order=count, **payload.model_dump())
    db.add(rule)
    await db.commit()
    return _serialize_dummy_epg_rule(rule)


class DummyEpgRuleUpdate(BaseModel):
    name: str | None = None
    pattern: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


@router.patch("/{playlist_id}/dummy-epg-rules/{rule_id}")
async def update_dummy_epg_rule(
    playlist_id: int, rule_id: int, payload: DummyEpgRuleUpdate, db: DbSession, _admin: AdminUser
) -> dict:
    rule = await db.get(DummyEpgRule, rule_id)
    if rule is None or rule.playlist_id != playlist_id:
        raise HTTPException(404, "Rule not found")
    updates = payload.model_dump(exclude_unset=True)
    if "pattern" in updates:
        _validate_pattern_or_400(updates["pattern"])
    for key, value in updates.items():
        setattr(rule, key, value)
    await db.commit()
    return _serialize_dummy_epg_rule(rule)


@router.delete("/{playlist_id}/dummy-epg-rules/{rule_id}")
async def delete_dummy_epg_rule(playlist_id: int, rule_id: int, db: DbSession, _admin: AdminUser) -> dict:
    rule = await db.get(DummyEpgRule, rule_id)
    if rule is None or rule.playlist_id != playlist_id:
        raise HTTPException(404, "Rule not found")
    await db.delete(rule)
    await db.commit()
    return {"ok": True}


@router.post("/{playlist_id}/dummy-epg-rules/reorder")
async def reorder_dummy_epg_rules(playlist_id: int, items: list[ReorderItem], db: DbSession, _admin: AdminUser) -> dict:
    positions = {item.id: item.sort_order for item in items}
    result = await db.execute(select(DummyEpgRule).where(DummyEpgRule.id.in_(positions.keys())))
    for rule in result.scalars().all():
        if rule.playlist_id == playlist_id:
            rule.sort_order = positions[rule.id]
    await db.commit()
    return {"ok": True}


class DummyEpgRuleTestIn(BaseModel):
    pattern: str
    sample_name: str
    timezone: str | None = None


@router.post("/{playlist_id}/dummy-epg-rules/test")
async def test_dummy_epg_rule(playlist_id: int, payload: DummyEpgRuleTestIn, _admin: AdminUser) -> dict:
    """Tries a not-yet-saved pattern (and timezone) against a sample channel name so the admin
    can see whether it matches, and what it parses out, before committing to it."""
    try:
        compiled = dummy_epg.validate_rule_pattern(payload.pattern)
    except ValueError as exc:
        return {"matched": False, "error": str(exc)}
    result = dummy_epg.parse_event_datetime(payload.sample_name, custom_patterns=[(compiled, payload.timezone)])
    if result is None:
        return {"matched": False, "error": None}
    event_start, title = result
    return {"matched": True, "error": None, "start": event_start.isoformat(), "title": title}


class DummyEpgRuleSuggestIn(BaseModel):
    sample_name: str


@router.post("/{playlist_id}/dummy-epg-rules/suggest")
async def suggest_dummy_epg_rule(playlist_id: int, payload: DummyEpgRuleSuggestIn, _admin: AdminUser) -> dict:
    """Reverse-engineers a candidate rule pattern from one real channel name, so an admin doesn't
    have to hand-write regex - just point it at a channel and review/tweak/save the suggestion."""
    suggestion = dummy_epg.suggest_rule_pattern(payload.sample_name)
    if suggestion is None:
        return {"suggested": False}
    return {
        "suggested": True,
        "pattern": suggestion.pattern,
        "start": suggestion.start.isoformat(),
        "title": suggestion.title,
    }


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
