import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.base import DummyEpgMode
from app.models.epg import EpgProgram
from app.models.playlist import DummyEpgRule, Playlist, PlaylistChannel
from app.services import dummy_epg
from app.services.channel_logo import resolve_channel_logo

logger = logging.getLogger("dptv.epg_writer")

DEFAULT_WINDOW_HOURS = 24 * 3


def xmltv_channel_id(pc: PlaylistChannel) -> str:
    """The `<channel id>` this channel's guide data is filed under in build_xmltv() below -
    always this synthetic id, never the provider's own raw EPG channel id string, so it stays
    stable and always present even for a channel with no real EPG mapping (dummy EPG still needs
    *some* id to file its generated programmes under). Every other place that tells a player
    which EPG channel a stream corresponds to (the M3U's tvg-id, the XC API's epg_channel_id
    field) has to emit this exact same value - a player matches a stream to its guide entirely
    by this id, so any divergence between "the id we handed out" and "the id xmltv.php actually
    uses" silently produces a channel with no guide, however correct the underlying program data
    is (this bit developers before - the XC API and M3U used to hand out the provider's raw
    epg_channel_id instead, which never appeared anywhere in xmltv.php's own channel ids)."""
    return f"pc{pc.id}"


def _xmltv_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S %z")


def resolve_dummy_mode(pc: PlaylistChannel) -> DummyEpgMode:
    """The dummy EPG mode this channel actually uses once "Inherit" is resolved against its
    category's default - exported (not module-private) because sync_engine's auto-mapper needs
    it too, to avoid silently real-EPG-mapping a channel an admin has deliberately configured (at
    either level) to use dummy EPG instead."""
    if pc.dummy_epg_mode != DummyEpgMode.INHERIT:
        return pc.dummy_epg_mode
    return pc.category.dummy_epg_mode


def _resolve_program_minutes(pc: PlaylistChannel) -> int:
    if pc.dummy_epg_program_minutes:
        return pc.dummy_epg_program_minutes
    return pc.category.dummy_epg_program_minutes


def _resolve_pinned_rule_id(pc: PlaylistChannel) -> int | None:
    """Which single rule (if any) EVENT mode should be pinned to for this channel - the
    channel's own pin always wins; otherwise, a channel left on "Inherit" picks up its
    category's default pin (see PlaylistCategory.dummy_epg_rule_id) so a channel added to a
    category later gets correct EPG without per-channel setup. A channel with its OWN mode
    explicitly set to EVENT (not inheriting) but no rule of its own isn't pinned by the
    category - that's an explicit per-channel choice to try every enabled rule."""
    if pc.dummy_epg_rule_id:
        return pc.dummy_epg_rule_id
    if pc.dummy_epg_mode == DummyEpgMode.INHERIT:
        return pc.category.dummy_epg_rule_id
    return None


async def _load_event_rules(
    db: AsyncSession, playlist_id: int
) -> tuple[list[tuple[re.Pattern, str | None]], dict[int, tuple[re.Pattern, str | None]]]:
    """Returns the enabled rules in sort_order (the "try everything" default for a channel with
    no rule pinned) alongside the same compiled rules keyed by id (for a channel pinned to one
    specific rule via PlaylistChannel.dummy_epg_rule_id - see compute_channel_programs below)."""
    result = await db.execute(
        select(DummyEpgRule)
        .where(DummyEpgRule.playlist_id == playlist_id, DummyEpgRule.enabled.is_(True))
        .order_by(DummyEpgRule.sort_order)
    )
    patterns: list[tuple[re.Pattern, str | None]] = []
    by_id: dict[int, tuple[re.Pattern, str | None]] = {}
    for rule in result.scalars().all():
        try:
            compiled = (dummy_epg.validate_rule_pattern(rule.pattern), rule.timezone)
        except ValueError:
            # Already validated on save - only reachable if a pattern was edited directly in the
            # DB. Skip rather than fail the whole XMLTV output over one bad rule.
            logger.warning("Skipping invalid dummy EPG rule %r (id=%s)", rule.name, rule.id)
            continue
        patterns.append(compiled)
        by_id[rule.id] = compiled
    return patterns, by_id


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
    event_rules, event_rules_by_id = await _load_event_rules(db, playlist_id)
    display_tz = dummy_epg.resolve_timezone(get_settings().display_timezone)

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

        if pc.sport_event_title and pc.sport_event_start:
            # A Live Sport category clone (see sport_refresh.py) - title/start/venue already
            # known exactly from the fetched fixture, so this bypasses dummy_epg_mode's
            # regex-based guessing entirely rather than trying to parse a date back out of the
            # channel's own auto-generated display name. Converted to the operator's configured
            # display timezone here (not stored that way) so the "Kick off HH:MM" text and the
            # "Up Next" countdown always reflect the *current* display_timezone setting, even if
            # it's changed after this channel was last refreshed.
            minutes = _resolve_program_minutes(pc)
            event_start_local = pc.sport_event_start.astimezone(display_tz)
            dummies = dummy_epg.generate_fixture_dummy(
                pc.sport_event_title,
                event_start_local,
                minutes,
                now,
                window_hours,
                venue_name=pc.sport_event_venue_name,
                venue_city=pc.sport_event_venue_city,
                venue_state=pc.sport_event_venue_state,
            )
            results.append(
                ChannelPrograms(
                    channel=pc,
                    programs=[
                        PreviewProgram(start=d.start, stop=d.stop, title=d.title, description=d.desc)
                        for d in dummies
                    ],
                )
            )
            continue

        mode = resolve_dummy_mode(pc)
        if mode == DummyEpgMode.OFF:
            results.append(ChannelPrograms(channel=pc))
            continue
        minutes = _resolve_program_minutes(pc)
        if mode == DummyEpgMode.EVENT:
            # A channel pinned to one specific rule (its own, or inherited from its category -
            # see _resolve_pinned_rule_id) only ever tries that rule, not every enabled playlist
            # rule - if the pinned rule was since disabled/deleted, this falls through to just
            # the built-in parser, same as a channel with no custom rules configured at all,
            # rather than silently trying rules the admin never selected for it.
            rule_id = _resolve_pinned_rule_id(pc)
            pinned = event_rules_by_id.get(rule_id) if rule_id else None
            channel_rules = [pinned] if pinned else ([] if rule_id else event_rules)
            dummies = dummy_epg.generate_event_dummy(pc.name, now, window_hours, minutes, custom_patterns=channel_rules)
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
        cid = xmltv_channel_id(pc)
        icon = resolve_channel_logo(pc)
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
