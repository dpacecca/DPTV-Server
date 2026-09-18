import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.epg import EpgProgram
from app.models.playlist import PlaylistCategory, PlaylistChannel
from app.services import sport_data
from app.services.name_normalize import normalize_name
from app.services.sport_data import Fixture

logger = logging.getLogger("dptv.sport_refresh")

# How far either side of a fixture's kickoff a currently-airing (or about-to-air) EPG program is
# still considered "showing this match" - wide enough to catch pre-match build-up and full-time
# analysis, and to match a future fixture's guide entry days before kickoff if the provider's
# XMLTV already has it scheduled, without also matching an unrelated program that happens to air
# hours away on the same channel.
_EPG_MATCH_BEFORE = timedelta(hours=1)
_EPG_MATCH_AFTER = timedelta(hours=4)


async def _matching_channels_for_fixture(
    db: AsyncSession, playlist_id: int, sport_category_id: int, fixture: Fixture
) -> list[PlaylistChannel]:
    """PlaylistChannels in the same playlist that look like they're showing `fixture`. Real EPG
    data (a program naming both teams, scheduled at or near the fixture's kickoff) is the
    preferred signal - it's what an admin would actually see in a guide, and works equally for a
    match live right now and one that's still days away. Falling back to the channel's own name
    catches channels with no EPG mapping (e.g. a dedicated "Sport 1" channel that never changes
    name) at the cost of being a little more prone to false positives."""
    home_norm, away_norm = normalize_name(fixture.home), normalize_name(fixture.away)
    if not home_norm or not away_norm:
        return []

    matched: dict[int, PlaylistChannel] = {}

    epg_result = await db.execute(
        select(PlaylistChannel, EpgProgram.title)
        .join(EpgProgram, EpgProgram.epg_channel_id == PlaylistChannel.epg_channel_id)
        .join(PlaylistCategory, PlaylistCategory.id == PlaylistChannel.playlist_category_id)
        .where(
            PlaylistCategory.playlist_id == playlist_id,
            PlaylistChannel.playlist_category_id != sport_category_id,
            PlaylistChannel.enabled.is_(True),
            EpgProgram.start <= fixture.kickoff + _EPG_MATCH_AFTER,
            EpgProgram.stop >= fixture.kickoff - _EPG_MATCH_BEFORE,
        )
    )
    # The time window alone only narrows down "airing around kickoff" - on a database with
    # thousands of EPG-mapped channels that's still nearly everything on air at that hour, so the
    # program's own title has to actually name both teams before its channel counts as a match.
    for pc, title in epg_result.all():
        norm_title = normalize_name(title)
        if home_norm in norm_title and away_norm in norm_title:
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


def _format_match_name(base_name: str, fixture: Fixture) -> str:
    """The whole point of a Live Sport category is knowing what's on without cross-referencing a
    fixture list elsewhere, so the match - not the underlying channel - is the headline; the
    channel it's actually playing on is kept as a suffix since the same match sometimes airs on
    more than one channel. Kickoff is always shown (rather than swapping in a "LIVE now" label)
    because it's still correct days later - this category only refreshes every few hours, so a
    "LIVE" label written at refresh time could sit there for hours after the match actually ends."""
    when = fixture.kickoff.astimezone(timezone.utc).strftime("%a %H:%M UTC")
    return f"{fixture.home} v {fixture.away} ({when}) - {base_name}"


async def refresh_sport_category(db: AsyncSession, category: PlaylistCategory) -> None:
    """Wipe-and-repopulate one Live Sport category's channels from the next few days of
    fixtures (see config.sport_lookahead_days) - live matches and ones still to come, not just
    whatever's live this exact minute. Safe to do unconditionally because a sport-managed
    category's channel list is never hand-edited (enforced at the API layer) - every row in it
    exists only because the last refresh put it there. On failure, leaves the existing channels
    alone (better to show stale matches than none) and just records the error for the admin to
    see."""
    assert category.sport_type is not None
    settings = get_settings()

    try:
        today = datetime.now(timezone.utc).date()
        fixtures: list[Fixture] = []
        for offset in range(settings.sport_lookahead_days):
            fixtures.extend(await sport_data.fetch_fixtures(category.sport_type, today + timedelta(days=offset)))
        upcoming = sorted((f for f in fixtures if not f.is_finished), key=lambda f: f.kickoff)

        # Keyed by (fixture, channel) rather than just channel id - the same channel can
        # legitimately show up more than once (e.g. a broadcaster airing a different match on
        # the same channel tomorrow), and each occurrence needs its own match-specific name.
        matched: dict[tuple[str, int], tuple[Fixture, PlaylistChannel]] = {}
        for fixture in upcoming:
            for pc in await _matching_channels_for_fixture(db, category.playlist_id, category.id, fixture):
                matched[(fixture.id, pc.id)] = (fixture, pc)
    except Exception as exc:  # noqa: BLE001 - record on the category, same as an EPG source fetch failure
        logger.exception("Failed to refresh sport category %s (%s)", category.id, category.sport_type)
        category.sport_last_refreshed_at = datetime.now(timezone.utc)
        category.sport_last_refresh_status = "failed"
        category.sport_last_refresh_error = str(exc)
        return

    await db.execute(delete(PlaylistChannel).where(PlaylistChannel.playlist_category_id == category.id))
    for sort_order, (fixture, pc) in enumerate(matched.values()):
        db.add(
            PlaylistChannel(
                playlist_category_id=category.id,
                source_channel_id=pc.source_channel_id,
                name=_format_match_name(pc.name, fixture),
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
