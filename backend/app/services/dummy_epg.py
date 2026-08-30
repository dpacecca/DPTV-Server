import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo as tzinfo_type
from zoneinfo import ZoneInfo, available_timezones

DATE_RE = re.compile(r"\b(?P<month>\d{1,2})[/\-](?P<day>\d{1,2})(?:[/\-](?P<year>\d{2,4}))?\b")
TIME_RE = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?\b")


def list_timezones() -> list[str]:
    """IANA zone names for the rule editor's timezone dropdown - proper Continent/City names
    plus UTC, not the legacy/deprecated aliases zoneinfo also returns."""
    names = {tz for tz in available_timezones() if "/" in tz} | {"UTC"}
    return sorted(names)


def resolve_timezone(tz_name: str | None) -> tzinfo_type:
    """A channel name never carries its own zone marker, so a rule's configured timezone (or
    UTC, if unset) is how the admin tells the parser which zone its hour/minute is expressed in.
    Falls back to UTC for a since-renamed/invalid zone name rather than failing the whole parse."""
    if not tz_name:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        return timezone.utc


@dataclass
class DummyProgram:
    start: datetime
    stop: datetime
    title: str


def _strip_matches(text: str, *matches: re.Match) -> str:
    spans = sorted((m.span() for m in matches if m), reverse=True)
    for start, end in spans:
        text = text[:start] + text[end:]
    # collapse leftover separators like " - " or double spaces/dashes.
    text = re.sub(r"[\-|:]{1,2}\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -|:")
    return text or text


_PIPE_PREFIX_RE = re.compile(r"^\s*[A-Z]{2,4}(?:\s*\([^)]*\))?\s*\|\s*")


def _clean_title(text: str) -> str:
    """Strips a leading "REGION (detail) | " style tag some providers glue onto every channel
    name (e.g. "AU (STAN 46) | Real Event Name") - never part of the actual event title.
    Deliberately narrow (a short ALL-CAPS region/network code, optionally with a parenthetical,
    right before the pipe) rather than "anything before the first |", so a title that legitimately
    contains a pipe (e.g. "Boxing: Fighter A | Fighter B") isn't damaged."""
    return _PIPE_PREFIX_RE.sub("", text, count=1).strip()


REQUIRED_RULE_GROUPS = ("hour", "minute")
OPTIONAL_RULE_GROUPS = ("ampm", "month", "day", "year", "title")


def validate_rule_pattern(pattern_text: str) -> re.Pattern:
    """Compiles a custom dummy-EPG rule pattern and checks it defines the group names the
    parser needs. Raises ValueError (not re.error) with a message meant to be shown to the
    admin who wrote the pattern, so the API layer can turn it straight into a 400."""
    try:
        compiled = re.compile(pattern_text)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc
    missing = [g for g in REQUIRED_RULE_GROUPS if g not in compiled.groupindex]
    if missing:
        raise ValueError(f"Pattern must include named group(s): {', '.join(f'(?P<{g}>...)' for g in missing)}")
    return compiled


def _build_event_datetime(
    hour_str: str, minute_str: str, ampm: str | None, month_str: str | None, day_str: str | None,
    year_str: str | None, now: datetime, tz: tzinfo_type = timezone.utc,
) -> datetime | None:
    """`now` stays UTC (the caller's reference clock); the returned datetime is in `tz` - the
    zone the embedded hour/minute is assumed to be expressed in - so it carries the correct
    offset all the way to XMLTV output without a separate "convert to local" step: a
    timezone-aware programme time is exactly what every XMLTV player already localizes for the
    viewer on its own."""
    hour = int(hour_str)
    minute = int(minute_str)
    ampm_norm = (ampm or "").lower().replace(".", "")
    if ampm_norm == "pm" and hour != 12:
        hour += 12
    elif ampm_norm == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return None

    if month_str and day_str:
        month, day = int(month_str), int(day_str)
        if year_str:
            year = int(year_str)
            if year < 100:
                year += 2000
        else:
            year = now.astimezone(tz).year
        try:
            return datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            return None

    now_local = now.astimezone(tz)
    event_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if event_dt < now_local - timedelta(hours=6):
        event_dt += timedelta(days=1)
    return event_dt


def _apply_custom_rule(
    channel_name: str, pattern: re.Pattern, now: datetime, tz: tzinfo_type = timezone.utc
) -> tuple[datetime, str] | None:
    m = pattern.search(channel_name)
    if not m:
        return None
    groups = m.groupdict()
    hour_str, minute_str = groups.get("hour"), groups.get("minute")
    if not hour_str or not minute_str:
        return None
    event_dt = _build_event_datetime(
        hour_str, minute_str, groups.get("ampm"), groups.get("month"), groups.get("day"), groups.get("year"), now, tz
    )
    if event_dt is None:
        return None
    title = groups.get("title")
    if not title:
        title = _strip_matches(channel_name, m)
    return event_dt, _clean_title(title)


def parse_event_datetime(
    channel_name: str,
    now: datetime | None = None,
    custom_patterns: list[tuple[re.Pattern, str | None]] | None = None,
) -> tuple[datetime, str] | None:
    """Extract a date/time embedded in a channel name, e.g. 'Team A vs Team B 08/24 8:00PM ET'.

    Tries each of `custom_patterns` - (compiled pattern, IANA timezone name or None for UTC)
    pairs - in order first (playlist-configured rules, for naming conventions the built-in
    parser doesn't handle: different date order, different separators, a source timezone other
    than UTC, a title that needs its own capture group instead of "everything but the
    date/time"), falling back to the built-in UTC month/day + time parser below if none match.

    Returns (event_start, cleaned_title) or None if no time could be found. event_start is
    timezone-aware in whichever zone actually matched.
    """
    now = now or datetime.now(timezone.utc)

    for pattern, tz_name in custom_patterns or []:
        result = _apply_custom_rule(channel_name, pattern, now, resolve_timezone(tz_name))
        if result:
            return result

    time_match = TIME_RE.search(channel_name)
    if not time_match:
        return None
    date_match = DATE_RE.search(channel_name)

    event_dt = _build_event_datetime(
        time_match.group("hour"),
        time_match.group("minute"),
        time_match.group("ampm"),
        date_match.group("month") if date_match else None,
        date_match.group("day") if date_match else None,
        date_match.group("year") if date_match else None,
        now,
    )
    if event_dt is None:
        return None

    title = _strip_matches(channel_name, time_match, date_match) if date_match else _strip_matches(
        channel_name, time_match
    )
    return event_dt, _clean_title(title)


_ISO_DATE_RE = re.compile(r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})")
_SUGGEST_DATE_RE = re.compile(r"(?P<num1>\d{1,2})(?P<sep>[/\-])(?P<num2>\d{1,2})(?:(?P=sep)(?P<year>\d{2,4}))?")


@dataclass
class RuleSuggestion:
    pattern: str
    start: datetime
    title: str


def suggest_rule_pattern(sample_name: str, now: datetime | None = None) -> RuleSuggestion | None:
    """Reverse-engineers a candidate custom dummy-EPG rule pattern from one real channel name, so
    an admin doesn't have to hand-write regex - just point it at a channel and review/tweak/save
    the suggestion.

    Detects an embedded time (and, if present, a date near it) using the same shapes the
    built-in parser understands, infers the date's month/day order from context (an
    out-of-range value settles it; otherwise the separator is used as a convention signal -
    "/" defaults to month/day like the built-in parser, "-" defaults to day/month, since that's
    the split this app's own rule examples already use), and generates a *general* pattern - not
    a literal copy of this one name - using \\d{1,2}/\\s+ shapes so it also matches sibling
    channels that follow the same naming convention with different values.
    """
    now = now or datetime.now(timezone.utc)
    time_match = TIME_RE.search(sample_name)
    if not time_match:
        return None

    # ISO-shaped "YYYY-MM-DD" is checked first and takes priority - it's unambiguous (a 4-digit
    # leading year can't be mistaken for a day/month), whereas the generic 2-part scan below
    # would otherwise mis-parse it (e.g. reading "26-08" out of the tail of "2026-08-30").
    iso_match = _ISO_DATE_RE.search(sample_name)
    date_match = iso_match or _SUGGEST_DATE_RE.search(sample_name)

    date_fragment = None
    if iso_match:
        date_fragment = r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    elif date_match:
        sep = date_match.group("sep")
        num1, num2 = int(date_match.group("num1")), int(date_match.group("num2"))
        if num1 > 12 and num2 <= 12:
            month_first = False
        elif num2 > 12 and num1 <= 12:
            month_first = True
        else:
            month_first = sep == "/"
        esc_sep = re.escape(sep)
        if month_first:
            date_fragment = rf"(?P<month>\d{{1,2}}){esc_sep}(?P<day>\d{{1,2}})"
        else:
            date_fragment = rf"(?P<day>\d{{1,2}}){esc_sep}(?P<month>\d{{1,2}})"
        if date_match.group("year"):
            date_fragment += rf"{esc_sep}(?P<year>\d{{2,4}})"

    time_fragment = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    if time_match.group("ampm"):
        time_fragment += r"\s*(?P<ampm>[AaPp]\.?[Mm]\.?)"

    if date_match and date_match.start() < time_match.start():
        fragments = [date_fragment, time_fragment]
        match_start, match_end = date_match.start(), time_match.end()
    elif date_match:
        fragments = [time_fragment, date_fragment]
        match_start, match_end = time_match.start(), date_match.end()
    else:
        fragments = [time_fragment]
        match_start, match_end = time_match.start(), time_match.end()
    joined = r"\s+".join(fragments)

    prefix = sample_name[:match_start].strip(" -|:()")
    suffix = sample_name[match_end:].strip(" -|:()")
    if prefix:
        # A permissive [\s\-:|()]+ boundary (not just \s+) so a connective dash/colon/pipe/paren
        # right before the date/time - "UFC 300 - 15-09-2026...", "Title 2026 (2026-08-30
        # 20:50:29)" - separates from the title instead of being swallowed into it.
        pattern = rf"(?P<title>.+?)[\s\-:|()]+{joined}"
    elif suffix:
        pattern = rf"{joined}[\s\-:|()]+(?P<title>.+)"
    else:
        pattern = joined

    try:
        compiled = validate_rule_pattern(pattern)
    except ValueError:
        return None
    parsed = _apply_custom_rule(sample_name, compiled, now)
    if parsed is None:
        return None
    start, title = parsed
    return RuleSuggestion(pattern=pattern, start=start, title=title)


def generate_name_dummy(
    channel_name: str, window_start: datetime, window_hours: int, program_minutes: int
) -> list[DummyProgram]:
    program_minutes = max(program_minutes, 5)
    slot_start = window_start.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=window_hours)
    programs: list[DummyProgram] = []
    while slot_start < window_end:
        slot_end = slot_start + timedelta(minutes=program_minutes)
        programs.append(DummyProgram(start=slot_start, stop=slot_end, title=channel_name))
        slot_start = slot_end
    return programs


UP_NEXT_BLOCK_MINUTES = 180


FINISHED_TITLE = "Scheduled event finished"


def _format_local_time(dt: datetime) -> str:
    """"9:00 PM", not "09:00 PM" - dt is already in whichever zone it should display as."""
    return dt.strftime("%I:%M %p").lstrip("0") or dt.strftime("%I:%M %p")


def _format_local_date(dt: datetime) -> str:
    """"31/8/2026" - day/month/year, no leading zeros."""
    return f"{dt.day}/{dt.month}/{dt.year}"


def _tile(start: datetime, end: datetime, title: str) -> list[DummyProgram]:
    """Fixed UP_NEXT_BLOCK_MINUTES-sized blocks covering [start, end), last one clipped -
    shared by both the pre-event countdown and the post-event filler below."""
    programs: list[DummyProgram] = []
    slot_start = start
    while slot_start < end:
        slot_end = min(slot_start + timedelta(minutes=UP_NEXT_BLOCK_MINUTES), end)
        programs.append(DummyProgram(start=slot_start, stop=slot_end, title=title))
        slot_start = slot_end
    return programs


def generate_event_dummy(
    channel_name: str,
    window_start: datetime,
    window_hours: int,
    program_minutes: int,
    custom_patterns: list[tuple[re.Pattern, str | None]] | None = None,
) -> list[DummyProgram]:
    parsed = parse_event_datetime(channel_name, now=window_start, custom_patterns=custom_patterns)
    if parsed is None:
        return generate_name_dummy(channel_name, window_start, window_hours, program_minutes)

    event_start, title = parsed
    display_title = title or channel_name
    event_stop = event_start + timedelta(minutes=max(program_minutes, 15))

    filler_start = window_start.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=window_hours)

    # Countdown to the event: fixed 3-hour "Up Next" blocks (not one giant filler) so a guide
    # grid repeatedly shows what's coming and when, in the same zone the event itself displays in.
    # Capped at window_end like every other filler here - an event days beyond the requested
    # window (e.g. a far-future PPV date) must not blow up into hundreds of countdown blocks.
    up_next_title = (
        f"Up Next: {display_title} starts {_format_local_time(event_start)} on {_format_local_date(event_start)}"
    )
    before = _tile(filler_start, min(event_start, window_end), up_next_title)

    # Same 3-hour tiling after the event ends, so the guide doesn't fall back to one giant block
    # (or the raw channel name) once it's over.
    after = _tile(event_stop, window_end, FINISHED_TITLE)

    return before + [DummyProgram(start=event_start, stop=event_stop, title=display_title)] + after
