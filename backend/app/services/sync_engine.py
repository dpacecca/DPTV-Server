from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import ChannelType, EpgMatchType, SyncStatus, SyncTrigger
from app.models.epg import EpgChannel, EpgSource
from app.models.playlist import PlaylistCategorySourceLink, PlaylistChannel
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.sync import SyncRun
from app.services import epg_mapper
from app.services.epg_parser import parse_xmltv
from app.services.xtream_client import ChannelData, XtreamClient, fetch_m3u_categories_and_channels
from app.models.base import SourceType

import httpx


async def sync_source(db: AsyncSession, source: Source) -> dict:
    run_started = datetime.now(timezone.utc)
    summary = {"categories_added": 0, "channels_added": 0, "channels_removed": 0, "channels_updated": 0}

    channel_types = [ChannelType.LIVE]
    if not source.ignore_vod:
        channel_types.append(ChannelType.VOD)
    if not source.ignore_series:
        channel_types.append(ChannelType.SERIES)

    fetched_category_data = []
    fetched_channel_data: list[ChannelData] = []

    if source.type == SourceType.XTREAM:
        client = XtreamClient(source)
        for ct in channel_types:
            cats, chans = await client.fetch_categories_and_channels(ct)
            fetched_category_data.extend(cats)
            fetched_channel_data.extend(chans)
    else:
        cats, chans = await fetch_m3u_categories_and_channels(source)
        fetched_category_data = cats
        fetched_channel_data = chans

    # --- Upsert categories ---
    existing_cats_result = await db.execute(select(SourceCategory).where(SourceCategory.source_id == source.id))
    existing_cats = {(c.external_id, c.channel_type): c for c in existing_cats_result.scalars().all()}

    seen_cat_keys = set()
    cat_by_key: dict[tuple, SourceCategory] = {}
    for cd in fetched_category_data:
        key = (cd.external_id, cd.channel_type)
        seen_cat_keys.add(key)
        cat = existing_cats.get(key)
        if cat is None:
            cat = SourceCategory(
                source_id=source.id,
                external_id=cd.external_id,
                name=cd.name,
                channel_type=cd.channel_type,
                enabled=source.auto_enable_new_groups,
            )
            db.add(cat)
            summary["categories_added"] += 1
        else:
            cat.name = cd.name
            cat.removed_at = None
        cat_by_key[key] = cat
    await db.flush()

    for key, cat in existing_cats.items():
        if key not in seen_cat_keys and cat.removed_at is None:
            cat.removed_at = run_started

    # --- Upsert channels ---
    existing_chans_result = await db.execute(
        select(SourceChannel).join(SourceCategory).where(SourceCategory.source_id == source.id)
    )
    existing_by_key = {
        (c.source_category_id, c.external_stream_id): c for c in existing_chans_result.scalars().all()
    }

    new_channel_rows: list[SourceChannel] = []
    seen_chan_ids: set[int] = set()

    for ch in fetched_channel_data:
        cat_key = (ch.category_external_id, ch.stream_type)
        cat = cat_by_key.get(cat_key)
        if cat is None:
            continue
        lookup_key = (cat.id, ch.external_stream_id)
        existing = existing_by_key.get(lookup_key)
        if existing is None:
            row = SourceChannel(
                source_category_id=cat.id,
                external_stream_id=ch.external_stream_id,
                name=ch.name,
                stream_type=ch.stream_type,
                tvg_id=ch.tvg_id,
                logo_url=ch.logo_url,
                container_extension=ch.container_extension,
                stream_url=ch.stream_url,
                first_seen_at=run_started,
                last_seen_at=run_started,
            )
            db.add(row)
            new_channel_rows.append(row)
            summary["channels_added"] += 1
        else:
            existing.name = ch.name
            existing.tvg_id = ch.tvg_id or existing.tvg_id
            existing.logo_url = ch.logo_url or existing.logo_url
            existing.stream_url = ch.stream_url or existing.stream_url
            existing.last_seen_at = run_started
            if existing.removed_at is not None:
                existing.removed_at = None
            summary["channels_updated"] += 1
            seen_chan_ids.add(existing.id)

    await db.flush()

    for (cat_id, _ext_id), existing in existing_by_key.items():
        if existing.id not in seen_chan_ids and existing not in new_channel_rows and existing.removed_at is None:
            existing.removed_at = run_started
            summary["channels_removed"] += 1

    # --- Propagate renames to non-locked playlist channels ---
    result = await db.execute(
        select(PlaylistChannel, SourceChannel)
        .join(SourceChannel, PlaylistChannel.source_channel_id == SourceChannel.id)
        .where(SourceChannel.source_category_id.in_([c.id for c in cat_by_key.values()]))
        .where(PlaylistChannel.name_locked.is_(False))
    )
    for pc, sc in result.all():
        if pc.name != sc.name:
            pc.name = sc.name

    # --- Auto-import new channels into linked playlist categories (New Channel Manager) ---
    if new_channel_rows:
        link_result = await db.execute(
            select(PlaylistCategorySourceLink).where(
                PlaylistCategorySourceLink.source_category_id.in_([c.id for c in cat_by_key.values()])
            )
        )
        links_by_source_cat: dict[int, list[int]] = {}
        for link in link_result.scalars().all():
            links_by_source_cat.setdefault(link.source_category_id, []).append(link.playlist_category_id)

        for row in new_channel_rows:
            for playlist_category_id in links_by_source_cat.get(row.source_category_id, []):
                db.add(
                    PlaylistChannel(
                        playlist_category_id=playlist_category_id,
                        source_channel_id=row.id,
                        name=row.name,
                        enabled=True,
                    )
                )

    source.last_sync_at = run_started
    source.last_sync_status = SyncStatus.SUCCESS.value
    source.last_sync_error = None
    await db.flush()
    return summary


async def sync_epg_source(db: AsyncSession, epg_source: EpgSource) -> dict:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(epg_source.url)
        resp.raise_for_status()
        raw = resp.content

    parsed_channels, parsed_programs = parse_xmltv(raw)

    # Full replace: simplest correct approach for XMLTV re-fetches.
    existing_result = await db.execute(select(EpgChannel).where(EpgChannel.epg_source_id == epg_source.id))
    existing_by_ext_id = {c.epg_channel_id: c for c in existing_result.scalars().all()}

    seen_ext_ids = set()
    channel_rows: dict[str, EpgChannel] = {}
    for pc in parsed_channels:
        seen_ext_ids.add(pc.epg_channel_id)
        row = existing_by_ext_id.get(pc.epg_channel_id)
        if row is None:
            row = EpgChannel(
                epg_source_id=epg_source.id,
                epg_channel_id=pc.epg_channel_id,
                display_name=pc.display_name,
                icon_url=pc.icon_url,
            )
            db.add(row)
        else:
            row.display_name = pc.display_name
            row.icon_url = pc.icon_url
        channel_rows[pc.epg_channel_id] = row

    for ext_id, row in existing_by_ext_id.items():
        if ext_id not in seen_ext_ids:
            await db.delete(row)

    await db.flush()

    # Wipe old programmes for this source's channels, bulk-insert fresh ones.
    from app.models.epg import EpgProgram

    channel_ids = [row.id for row in channel_rows.values()]
    if channel_ids:
        await db.execute(EpgProgram.__table__.delete().where(EpgProgram.epg_channel_id.in_(channel_ids)))

    to_insert = []
    for pp in parsed_programs:
        row = channel_rows.get(pp.channel_id)
        if row is None:
            continue
        to_insert.append(
            {
                "epg_channel_id": row.id,
                "start": pp.start,
                "stop": pp.stop,
                "title": pp.title,
                "subtitle": pp.subtitle,
                "description": pp.description,
                "category": pp.category,
            }
        )
    if to_insert:
        await db.execute(EpgProgram.__table__.insert(), to_insert)

    epg_source.last_refreshed_at = datetime.now(timezone.utc)
    epg_source.last_refresh_status = SyncStatus.SUCCESS.value
    epg_source.last_refresh_error = None
    await db.flush()
    return {"channels": len(parsed_channels), "programs": len(parsed_programs)}


async def apply_auto_clear(db: AsyncSession) -> int:
    sources_result = await db.execute(select(Source).where(Source.auto_clear_removed_days.is_not(None)))
    removed_count = 0
    for source in sources_result.scalars().all():
        threshold = datetime.now(timezone.utc) - timedelta(days=source.auto_clear_removed_days)
        result = await db.execute(
            select(SourceChannel)
            .join(SourceCategory)
            .where(SourceCategory.source_id == source.id)
            .where(SourceChannel.removed_at.is_not(None))
            .where(SourceChannel.removed_at < threshold)
        )
        stale_channels = result.scalars().all()
        if not stale_channels:
            continue
        stale_ids = [c.id for c in stale_channels]
        pc_result = await db.execute(select(PlaylistChannel).where(PlaylistChannel.source_channel_id.in_(stale_ids)))
        for pc in pc_result.scalars().all():
            await db.delete(pc)
            removed_count += 1
    await db.flush()
    return removed_count


async def auto_map_epg_for_unmapped_channels(db: AsyncSession, sensitivity: float = 0.9) -> int:
    epg_channels_result = await db.execute(select(EpgChannel))
    all_epg_channels = epg_channels_result.scalars().all()
    if not all_epg_channels:
        return 0

    unmapped_result = await db.execute(
        select(PlaylistChannel).where(PlaylistChannel.epg_match_type == EpgMatchType.NONE)
    )
    matched = 0
    for pc in unmapped_result.scalars().all():
        best = epg_mapper.auto_match(pc.name, all_epg_channels, sensitivity=sensitivity)
        if best is not None:
            pc.epg_channel_id = best.id
            pc.epg_match_type = EpgMatchType.AUTO
            matched += 1
    await db.flush()
    return matched


async def run_full_sync(db: AsyncSession, trigger: SyncTrigger, epg_sensitivity: float = 0.9) -> SyncRun:
    run = SyncRun(started_at=datetime.now(timezone.utc), trigger=trigger, status=SyncStatus.RUNNING, summary={})
    db.add(run)
    await db.flush()

    summary: dict = {"sources": {}, "epg_sources": {}, "errors": []}
    try:
        sources_result = await db.execute(select(Source).where(Source.enabled.is_(True)))
        for source in sources_result.scalars().all():
            try:
                summary["sources"][source.name] = await sync_source(db, source)
            except Exception as exc:  # noqa: BLE001 - one bad source shouldn't kill the run
                source.last_sync_status = SyncStatus.FAILED.value
                source.last_sync_error = str(exc)
                summary["errors"].append(f"source:{source.name}: {exc}")

        epg_sources_result = await db.execute(select(EpgSource))
        for epg_source in epg_sources_result.scalars().all():
            try:
                summary["epg_sources"][epg_source.name] = await sync_epg_source(db, epg_source)
            except Exception as exc:  # noqa: BLE001
                epg_source.last_refresh_status = SyncStatus.FAILED.value
                epg_source.last_refresh_error = str(exc)
                summary["errors"].append(f"epg:{epg_source.name}: {exc}")

        summary["auto_cleared_channels"] = await apply_auto_clear(db)
        summary["auto_mapped_channels"] = await auto_map_epg_for_unmapped_channels(db, sensitivity=epg_sensitivity)

        run.status = SyncStatus.PARTIAL if summary["errors"] else SyncStatus.SUCCESS
    except Exception as exc:  # noqa: BLE001
        run.status = SyncStatus.FAILED
        summary["errors"].append(f"fatal: {exc}")
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.summary = summary
        await db.flush()

    return run
