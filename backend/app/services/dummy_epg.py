import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DATE_RE = re.compile(r"\b(?P<month>\d{1,2})[/\-](?P<day>\d{1,2})(?:[/\-](?P<year>\d{2,4}))?\b")
TIME_RE = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?\b")


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
    year_str: str | None, now: datetime,
) -> datetime | None:
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
            year = now.year
        try:
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None

    event_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if event_dt < now - timedelta(hours=6):
        event_dt += timedelta(days=1)
    return event_dt


def _apply_custom_rule(channel_name: str, pattern: re.Pattern, now: datetime) -> tuple[datetime, str] | None:
    m = pattern.search(channel_name)
    if not m:
        return None
    groups = m.groupdict()
    hour_str, minute_str = groups.get("hour"), groups.get("minute")
    if not hour_str or not minute_str:
        return None
    event_dt = _build_event_datetime(
        hour_str, minute_str, groups.get("ampm"), groups.get("month"), groups.get("day"), groups.get("year"), now
    )
    if event_dt is None:
        return None
    title = groups.get("title")
    if not title:
        title = _strip_matches(channel_name, m)
    return event_dt, title.strip()


def parse_event_datetime(
    channel_name: str, now: datetime | None = None, custom_patterns: list[re.Pattern] | None = None
) -> tuple[datetime, str] | None:
    """Extract a date/time embedded in a channel name, e.g. 'Team A vs Team B 08/24 8:00PM ET'.

    Tries each of `custom_patterns` in order first (playlist-configured rules, for naming
    conventions the built-in parser doesn't handle - different date order, different separators,
    a title that needs its own capture group instead of "everything but the date/time"), falling
    back to the built-in month/day + time parser below if none of them match.

    Returns (event_start_utc, cleaned_title) or None if no time could be found.
    """
    now = now or datetime.now(timezone.utc)

    for pattern in custom_patterns or []:
        result = _apply_custom_rule(channel_name, pattern, now)
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
    return event_dt, title


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


def generate_event_dummy(
    channel_name: str,
    window_start: datetime,
    window_hours: int,
    program_minutes: int,
    custom_patterns: list[re.Pattern] | None = None,
) -> list[DummyProgram]:
    parsed = parse_event_datetime(channel_name, now=window_start, custom_patterns=custom_patterns)
    if parsed is None:
        return generate_name_dummy(channel_name, window_start, window_hours, program_minutes)

    event_start, title = parsed
    event_stop = event_start + timedelta(minutes=max(program_minutes, 15))
    programs = [DummyProgram(start=event_start, stop=event_stop, title=title or channel_name)]

    # Fill the rest of the requested window with a generic filler so players always show *something*.
    filler_start = window_start.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=window_hours)
    if filler_start < event_start:
        programs.insert(0, DummyProgram(start=filler_start, stop=event_start, title=channel_name))
    if event_stop < window_end:
        programs.append(DummyProgram(start=event_stop, stop=window_end, title=channel_name))
    return programs
