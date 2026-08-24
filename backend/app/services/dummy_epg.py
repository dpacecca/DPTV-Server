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


def parse_event_datetime(channel_name: str, now: datetime | None = None) -> tuple[datetime, str] | None:
    """Extract a date/time embedded in a channel name, e.g. 'Team A vs Team B 08/24 8:00PM ET'.

    Returns (event_start_utc, cleaned_title) or None if no time could be found.
    """
    now = now or datetime.now(timezone.utc)
    time_match = TIME_RE.search(channel_name)
    if not time_match:
        return None
    date_match = DATE_RE.search(channel_name)

    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute"))
    ampm = (time_match.group("ampm") or "").lower().replace(".", "")
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return None

    if date_match:
        month = int(date_match.group("month"))
        day = int(date_match.group("day"))
        year_raw = date_match.group("year")
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            year = now.year
        try:
            event_dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None
    else:
        event_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if event_dt < now - timedelta(hours=6):
            event_dt += timedelta(days=1)

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
    channel_name: str, window_start: datetime, window_hours: int, program_minutes: int
) -> list[DummyProgram]:
    parsed = parse_event_datetime(channel_name, now=window_start)
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
