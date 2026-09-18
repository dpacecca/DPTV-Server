import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.epg import EpgProgram
from app.models.playlist import PlaylistCategory, PlaylistChannel
from app.services import sport_data
from app.services.name_normalize import normalize_name

logger = logging.getLogger("dptv.sport_refresh")


async def _matching_channels_for_fixture(
    db: AsyncSession, playlist_id: int, sport_category_id: int, home: str, away: str
) -> list[PlaylistChannel]:
    """PlaylistChannels in the same playlist that look like they're showing `home` v `away`
    right now. Real EPG data (a program currently airing whose title names both teams) is the
    preferred signal - it's what an admin would actually see in a guide. Falling back to the
    channel's own name catches channels with no EPG mapping (e.g. a dedicated "Sport 1" channel
    that never changes name) at the cost of being a little more prone to false positives."""
    home_norm, away_norm = normalize_name(home), normalize_name(away)
    if not home_norm or not away_norm:
        return []

    now = datetime.now(timezone.utc)
    matched: dict[int, PlaylistChannel] = {}

    epg_result = await db.execute(
        select(PlaylistChannel)
        .join(EpgProgram, EpgProgram.epg_channel_id == PlaylistChannel.epg_channel_id)
        .join(PlaylistCategory, PlaylistCategory.id == PlaylistChannel.playlist_category_id)
        .where(
            PlaylistCategory.playlist_id == playlist_id,
            PlaylistChannel.playlist_category_id != sport_category_id,
            PlaylistChannel.enabled.is_(True),
            EpgProgram.start <= now,
            EpgProgram.stop >= now,
        )
    )
    for pc in epg_result.scalars().all():
        matched[pc.id] = pc

    name_result = await db.execute(
        select(PlaylistChannel)
        .join(PlaylistCategory, PlaylistCategory.id == PlaylistChannel.playlist_category_id)
        .where(
            PlaylistCategory.playlist_id == playlist_id,
            PlaylistChannel.playlist_category_id != sport_category_id,
            PlaylistChannel.enabled.is_(True),
        )
    )
    for pc in name_result.scalars().all():
        if pc.id in matched:
            continue
        norm = normalize_name(pc.name)
        if home_norm in norm and away_norm in norm:
            matched[pc.id] = pc

    return list(matched.values())


async def refresh_sport_category(db: AsyncSession, category: PlaylistCategory) -> None:
    """Wipe-and-repopulate one Live Sport category's channels from whatever's live right now.
    Safe to do unconditionally because a sport-managed category's channel list is never hand-
    edited (enforced at the API layer) - every row in it exists only because the last refresh
    put it there. On failure, leaves the existing channels alone (better to show stale matches
    than none) and just records the error for the admin to see."""
    assert category.sport_type is not None

    try:
        today = datetime.now(timezone.utc).date()
        fixtures = await sport_data.fetch_fixtures(category.sport_type, today)
        live_fixtures = [f for f in fixtures if f.is_live]

        matched_by_id: dict[int, PlaylistChannel] = {}
        for fixture in live_fixtures:
            for pc in await _matching_channels_for_fixture(db, category.playlist_id, category.id, fixture.home, fixture.away):
                matched_by_id[pc.id] = pc
    except Exception as exc:  # noqa: BLE001 - record on the category, same as an EPG source fetch failure
        logger.exception("Failed to refresh sport category %s (%s)", category.id, category.sport_type)
        category.sport_last_refreshed_at = datetime.now(timezone.utc)
        category.sport_last_refresh_status = "failed"
        category.sport_last_refresh_error = str(exc)
        return

    await db.execute(delete(PlaylistChannel).where(PlaylistChannel.playlist_category_id == category.id))
    for sort_order, pc in enumerate(matched_by_id.values()):
        db.add(
            PlaylistChannel(
                playlist_category_id=category.id,
                source_channel_id=pc.source_channel_id,
                name=pc.name,
                manual_stream_url=pc.manual_stream_url,
                number=pc.number,
                logo_url_override=pc.logo_url_override,
                enabled=True,
                sort_order=sort_order,
                epg_channel_id=pc.epg_channel_id,
                epg_match_type=pc.epg_match_type,
            )
        )

    category.sport_last_refreshed_at = datetime.now(timezone.utc)
    category.sport_last_refresh_status = "success"
    category.sport_last_refresh_error = None


async def refresh_all_sport_categories(db: AsyncSession) -> int:
    """Refreshes every Live Sport category across every playlist. Failures are isolated per
    category (see refresh_sport_category) so one provider outage or bad category doesn't stop
    the rest from refreshing. Returns how many categories were processed."""
    result = await db.execute(select(PlaylistCategory).where(PlaylistCategory.sport_type.is_not(None)))
    categories = result.scalars().all()
    for category in categories:
        await refresh_sport_category(db, category)
        await db.commit()
    return len(categories)
