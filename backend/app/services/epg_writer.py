import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.base import ChannelType, DummyEpgMode
from app.models.epg import EpgProgram
from app.models.playlist import DummyEpgRule, Playlist, PlaylistChannel
from app.services import dummy_epg
from app.services.channel_logo import resolve_channel_logo

logger = logging.getLogger("dptv.epg_writer")

DEFAULT_WINDOW_HOURS = 24 * 3

# Every token the EPG Layout settings (epg_title_layout/epg_description_layout, see config.py)
# can be built from, mirroring IPTVBoss's own EPG layout editor. NEXT_LINE is a line break, not a
# field - everything else resolves to a piece of text (possibly empty, if that program has none
# of it) that compose_program_text() below joins together.
EPG_LAYOUT_TOKENS = (
    "title",
    "subtitle_sports",
    "subtitle_nonsports",
    "description",
    "episode_number",
    "movie_year",
    "episode_or_year",
    "new_tag",
    "next_line",
)

_SPORTS_CATEGORY_RE = re.compile(r"sport", re.IGNORECASE)


def _is_sports(prog: EpgProgram, channel_hint: str | None) -> bool:
    """Prefers the program's own <category> (precise - this one programme really is sports),
    but falls back to the channel's own name/category (e.g. "Sky Sport 1") when a provider's
    guide doesn't send <category> per-programme at all - common enough on dedicated sports
    channels that SUBTITLE_SPORTS would otherwise just never fire for them."""
    if prog.category and _SPORTS_CATEGORY_RE.search(prog.category):
        return True
    return bool(channel_hint and _SPORTS_CATEGORY_RE.search(channel_hint))


def _movie_year(prog: EpgProgram) -> str:
    # A <date> can be a bare year or a full YYYYMMDD - either way the year is its first 4
    # digits. Not a year at all (too short, non-digit) just contributes nothing, same as a
    # program with no <date> - this is a best-effort cosmetic, not a field worth failing over.
    raw = (prog.program_date or "").strip()
    return raw[:4] if len(raw) >= 4 and raw[:4].isdigit() else ""


def _layout_token_value(token: str, prog: EpgProgram, channel_hint: str | None, channel_type: ChannelType | None) -> str:
    if token == "title":
        return prog.title or ""
    if token == "subtitle_sports":
        return (prog.subtitle or "") if _is_sports(prog, channel_hint) else ""
    if token == "subtitle_nonsports":
        return (prog.subtitle or "") if not _is_sports(prog, channel_hint) else ""
    if token == "description":
        return prog.description or ""
    if token == "episode_number":
        return prog.episode_num or ""
    if token == "movie_year":
        return _movie_year(prog)
    if token == "episode_or_year":
        # The one token that adapts to what it's actually labeling: a TV Shows channel's episode
        # number ("S2 E14") matters far more than a release year that's rarely even present on a
        # per-episode basis, while a Movies channel has no episode number at all - only a year.
        # The plain, non-adaptive movie_number/episode_number tokens above are untouched by this
        # - they always mean exactly what their name says, for an admin who wants that literally.
        if channel_type == ChannelType.SERIES:
            return prog.episode_num or ""
        return _movie_year(prog)
    if token == "new_tag":
        return "NEW" if prog.is_new else ""
    return ""


def parse_layout(raw: str) -> list[str]:
    """A stored epg_title_layout/epg_description_layout value (comma-separated) -> the tokens an
    admin actually picked, lowercased and validated against EPG_LAYOUT_TOKENS so a hand-edited or
    stale .env value can't inject something compose_program_text() doesn't recognize."""
    return [t for t in (tok.strip().lower() for tok in raw.split(",")) if t in EPG_LAYOUT_TOKENS]


def compose_program_text(
    prog: EpgProgram, channel_hint: str | None = None, channel_type: ChannelType | None = None
) -> tuple[str, str]:
    """The (title, description) every guide-facing surface actually shows for a real (non-dummy)
    EPG program - XMLTV output, the XC API's short-EPG action, and the admin UI's EPG preview all
    call this rather than reading prog.title/.description directly, so they can never disagree.
    channel_hint (e.g. the channel's own name, "NZ: Sky Sport 1") is the sports-detection
    fallback for a provider whose guide never sends <category> per-programme - see _is_sports.
    channel_type decides what episode_or_year resolves to (see _layout_token_value) - omit it
    (e.g. a caller with no channel/category in scope) and that token just falls back to year.
    Assembled from the admin's configured EPG Layout (see config.py's epg_title_layout/
    epg_description_layout) - each token resolves to a piece of text (empty if this particular
    program doesn't have it, e.g. subtitle_sports on a non-sports program, or episode_number with
    no <episode-num> in the source guide), non-empty pieces on the same line join with a space,
    and next_line starts a new line (collapsing away if either side of it turned out empty, so a
    missing optional field never leaves a stray blank line)."""
    settings = get_settings()

    def build(raw_layout: str) -> str:
        lines: list[list[str]] = [[]]
        for token in parse_layout(raw_layout):
            if token == "next_line":
                lines.append([])
                continue
            value = _layout_token_value(token, prog, channel_hint, channel_type)
            if value:
                lines[-1].append(value)
        return "\n".join(" ".join(line) for line in lines if line)

    title = build(settings.epg_title_layout) or prog.title or ""
    description = build(settings.epg_description_layout)
    return title, (description or None)


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


@dataclass
class _LoadedRule:
    id: int
    compiled: tuple[re.Pattern, str | None]


async def _load_event_rules(db: AsyncSession, playlist_id: int) -> tuple[list[_LoadedRule], dict[int, _LoadedRule]]:
    """Returns the enabled rules in sort_order (the "try everything" default for a channel with
    no rule pinned) alongside the same rules keyed by id (for a channel pinned to one specific
    rule via PlaylistChannel.dummy_epg_rule_id - see compute_channel_programs below)."""
    result = await db.execute(
        select(DummyEpgRule)
        .where(DummyEpgRule.playlist_id == playlist_id, DummyEpgRule.enabled.is_(True))
        .order_by(DummyEpgRule.sort_order)
    )
    ordered: list[_LoadedRule] = []
    by_id: dict[int, _LoadedRule] = {}
    for rule in result.scalars().all():
        try:
            compiled = (dummy_epg.validate_rule_pattern(rule.pattern), rule.timezone)
        except (ValueError, TypeError):
            # Already validated on save - only reachable if a pattern was edited directly in
            # the DB. Skip rather than fail the whole XMLTV output over one bad rule.
            logger.warning("Skipping invalid dummy EPG rule %r (id=%s)", rule.name, rule.id)
            continue
        loaded = _LoadedRule(id=rule.id, compiled=compiled)
        ordered.append(loaded)
        by_id[rule.id] = loaded
    return ordered, by_id


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
            preview_programs = []
            for p in real_programs:
                title, description = compose_program_text(
                    p, channel_hint=pc.name, channel_type=pc.category.channel_type
                )
                preview_programs.append(PreviewProgram(start=p.start, stop=p.stop, title=title, description=description))
            results.append(ChannelPrograms(channel=pc, programs=preview_programs))
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

            channel_rules: list[tuple[re.Pattern, str | None]] = []
            if pinned:
                channel_rules = [pinned.compiled]
            elif not rule_id:
                channel_rules = [r.compiled for r in event_rules]

            dummies = dummy_epg.generate_event_dummy(
                pc.name, now, window_hours, minutes, custom_patterns=channel_rules
            )
        else:
            dummies = dummy_epg.generate_name_dummy(pc.name, now, window_hours, minutes)
        results.append(
            ChannelPrograms(
                channel=pc,
                programs=[
                    PreviewProgram(start=d.start, stop=d.stop, title=d.title, description=d.desc)
                    for d in dummies
                ],
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
