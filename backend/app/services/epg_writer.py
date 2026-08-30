import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import DummyEpgMode
from app.models.epg import EpgProgram
from app.models.playlist import DummyEpgRule, Playlist, PlaylistChannel
from app.services import dummy_epg

logger = logging.getLogger("dptv.epg_writer")

DEFAULT_WINDOW_HOURS = 24 * 3


def _xmltv_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S %z")


def _resolve_dummy_mode(pc: PlaylistChannel) -> DummyEpgMode:
    if pc.dummy_epg_mode != DummyEpgMode.INHERIT:
        return pc.dummy_epg_mode
    if pc.category.dummy_epg_for_unassigned:
        return DummyEpgMode.NAME
    return DummyEpgMode.OFF


def _resolve_program_minutes(pc: PlaylistChannel) -> int:
    if pc.dummy_epg_program_minutes:
        return pc.dummy_epg_program_minutes
    return pc.category.dummy_epg_program_minutes


async def _load_event_rules(db: AsyncSession, playlist_id: int) -> list[tuple[re.Pattern, str | None]]:
    result = await db.execute(
        select(DummyEpgRule)
        .where(DummyEpgRule.playlist_id == playlist_id, DummyEpgRule.enabled.is_(True))
        .order_by(DummyEpgRule.sort_order)
    )
    patterns: list[tuple[re.Pattern, str | None]] = []
    for rule in result.scalars().all():
        try:
            patterns.append((dummy_epg.validate_rule_pattern(rule.pattern), rule.timezone))
        except ValueError:
            # Already validated on save - only reachable if a pattern was edited directly in the
            # DB. Skip rather than fail the whole XMLTV output over one bad rule.
            logger.warning("Skipping invalid dummy EPG rule %r (id=%s)", rule.name, rule.id)
    return patterns


@dataclass
class PreviewProgram:
    start: datetime
    stop: datetime
    title: str
    description: str | None = None


@dataclass
class ChannelPrograms:
    channel: PlaylistChannel
    programs: list[PreviewProgram] = field(default_factory=list)
    """Real EPG programs if the channel has an EPG mapping and any fall inside the window;
    otherwise whatever the resolved dummy EPG mode generates (possibly empty, if mode is OFF)."""


async def compute_channel_programs(
    db: AsyncSession, channels: list[PlaylistChannel], playlist_id: int, window_hours: int = DEFAULT_WINDOW_HOURS
) -> list[ChannelPrograms]:
    """The shared core behind both XMLTV output and the category EPG preview: for each channel,
    real guide data where mapped, else dummy-generated programs (including custom event rules
    and "Up Next" blocks) - so a preview and the real feed can never silently disagree."""
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)
    event_rules = await _load_event_rules(db, playlist_id)

    real_epg_channel_ids = {pc.epg_channel_id for pc in channels if pc.epg_channel_id}
    programs_by_epg_channel: dict[int, list[EpgProgram]] = {}
    if real_epg_channel_ids:
        result = await db.execute(
            select(EpgProgram)
            .where(EpgProgram.epg_channel_id.in_(real_epg_channel_ids))
            .where(EpgProgram.stop > now)
            .where(EpgProgram.start < window_end)
            .order_by(EpgProgram.start)
        )
        for prog in result.scalars().all():
            programs_by_epg_channel.setdefault(prog.epg_channel_id, []).append(prog)

    results: list[ChannelPrograms] = []
    for pc in channels:
        real_programs = programs_by_epg_channel.get(pc.epg_channel_id) if pc.epg_channel_id else None
        if real_programs:
            results.append(
                ChannelPrograms(
                    channel=pc,
                    programs=[
                        PreviewProgram(start=p.start, stop=p.stop, title=p.title, description=p.description)
                        for p in real_programs
                    ],
                )
            )
            continue

        mode = _resolve_dummy_mode(pc)
        if mode == DummyEpgMode.OFF:
            results.append(ChannelPrograms(channel=pc))
            continue
        minutes = _resolve_program_minutes(pc)
        if mode == DummyEpgMode.EVENT:
            dummies = dummy_epg.generate_event_dummy(pc.name, now, window_hours, minutes, custom_patterns=event_rules)
        else:
            dummies = dummy_epg.generate_name_dummy(pc.name, now, window_hours, minutes)
        results.append(
            ChannelPrograms(
                channel=pc, programs=[PreviewProgram(start=d.start, stop=d.stop, title=d.title) for d in dummies]
            )
        )
    return results


async def build_xmltv(db: AsyncSession, playlist: Playlist, window_hours: int = DEFAULT_WINDOW_HOURS) -> bytes:
    all_channels: list[PlaylistChannel] = [
        pc for category in playlist.categories for pc in category.channels if pc.enabled
    ]
    channel_programs = await compute_channel_programs(db, all_channels, playlist.id, window_hours)

    channel_xml: list[str] = []
    programme_xml: list[str] = []
    for cp in channel_programs:
        pc = cp.channel
        cid = f"pc{pc.id}"
        icon = pc.logo_url_override or (pc.source_channel.logo_url if pc.source_channel else None)
        icon_tag = f'<icon src="{escape(icon)}"/>' if icon else ""
        channel_xml.append(f'<channel id="{cid}"><display-name>{escape(pc.name)}</display-name>{icon_tag}</channel>')
        for prog in cp.programs:
            desc = f"<desc>{escape(prog.description)}</desc>" if prog.description else ""
            programme_xml.append(
                f'<programme start="{_xmltv_time(prog.start)}" stop="{_xmltv_time(prog.stop)}" channel="{cid}">'
                f"<title>{escape(prog.title)}</title>{desc}</programme>"
            )

    body = "".join(channel_xml) + "".join(programme_xml)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="DPTV-Server">{body}</tv>\n'
    return xml.encode("utf-8")
