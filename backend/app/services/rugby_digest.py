from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.base import ChannelType
from app.models.playlist import Playlist, PlaylistCategory, PlaylistChannel
from app.services import allsports_rugby, gotify
from app.services.dummy_epg import resolve_timezone
from app.services.name_normalize import normalize_name
from app.services.sport_data import Fixture


async def _enabled_live_channels(db: AsyncSession) -> list[PlaylistChannel]:
    result = await db.execute(
        select(PlaylistChannel)
        .join(PlaylistCategory, PlaylistChannel.playlist_category_id == PlaylistCategory.id)
        .join(Playlist, PlaylistCategory.playlist_id == Playlist.id)
        .where(
            PlaylistChannel.enabled.is_(True),
            PlaylistCategory.channel_type == ChannelType.LIVE,
            Playlist.enabled.is_(True),
        )
    )
    return list(result.scalars().all())


def _find_channel(channels_by_norm: list[tuple[str, PlaylistChannel]], fixture: Fixture) -> PlaylistChannel | None:
    """Inverse of dummy_epg.match_fixture_for_channel: given one fixture, which (if any) of this
    server's own channels is showing it - same both-team-names-appear-as-substrings matching, run
    in the opposite direction since the digest needs "which channel for this match", not "which
    match for this channel"."""
    home_norm, away_norm = normalize_name(fixture.home), normalize_name(fixture.away)
    if not home_norm or not away_norm:
        return None
    for norm_name, pc in channels_by_norm:
        if home_norm in norm_name and away_norm in norm_name:
            return pc
    return None


def _format_digest(fixtures: list[Fixture], channels_by_norm: list[tuple[str, PlaylistChannel]], tz) -> tuple[str, int]:
    by_competition: dict[str, list[Fixture]] = {}
    for fx in sorted(fixtures, key=lambda f: f.kickoff):
        by_competition.setdefault(fx.competition, []).append(fx)

    matched_count = 0
    sections: list[str] = []
    for competition, fxs in by_competition.items():
        lines = [competition]
        for fx in fxs:
            local_kickoff = fx.kickoff.astimezone(tz)
            channel = _find_channel(channels_by_norm, fx)
            if channel is not None:
                matched_count += 1
            channel_text = channel.name if channel is not None else "no channel assigned"
            home = fx.home_display or fx.home
            away = fx.away_display or fx.away
            lines.append(f"{local_kickoff.strftime('%H:%M')}  {home} vs {away} - {channel_text}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections), matched_count


async def send_daily_rugby_digest(db: AsyncSession) -> dict:
    """Fetches today's matches (in display_timezone) across RUGBY_COMPETITIONS, matches each
    against this server's own enabled live channels, and pushes one Gotify notification listing
    them grouped by competition. Always sends something, even on a day with no matches at all, so
    a quiet day still confirms the job ran rather than looking indistinguishable from a failure -
    see the scheduled job in core/scheduler.py, and the manual "run now" trigger in
    api/routes/scheduler.py."""
    settings = get_settings()
    tz = resolve_timezone(settings.display_timezone)
    today = datetime.now(tz).date()

    fixtures = await allsports_rugby.fetch_rugby_matches(today)
    if not fixtures:
        await gotify.send_notification("Rugby Today", "No matches today in your followed competitions.")
        return {"matches": 0, "channels_matched": 0, "sent": True}

    channels = await _enabled_live_channels(db)
    channels_by_norm = [(normalize_name(pc.name), pc) for pc in channels]
    message, matched_count = _format_digest(fixtures, channels_by_norm, tz)

    title = f"Rugby Today ({len(fixtures)} match{'es' if len(fixtures) != 1 else ''})"
    await gotify.send_notification(title, message)
    return {"matches": len(fixtures), "channels_matched": matched_count, "sent": True}
