import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo as tzinfo_type
from zoneinfo import ZoneInfo, available_timezones

DATE_RE = re.compile(r"\b(?P<month>\d{1,2})[/\-](?P<day>\d{1,2})(?:[/\-](?P<year>\d{2,4}))?\b")
TIME_RE = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?\b")

_MONTH_NUMBERS = {
    name[:3]: i
    for i, name in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}


def _parse_month(month_str: str) -> int | None:
    """A rule built from a written-month hint (see suggest_rule_from_hints below) captures the
    month as text ("Sep", "September") rather than a number, since that's what actually appears
    in the channel name - so this has to accept either shape. Matches on the first 3 letters
    case-insensitively, which covers both abbreviated and full month names."""
    if month_str.isdigit():
        m = int(month_str)
        return m if 1 <= m <= 12 else None
    return _MONTH_NUMBERS.get(month_str.strip().lower()[:3])



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
        month = _parse_month(month_str)
        day = int(day_str)
        if month is None:
            return None
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

# Shared "something's between these fields" boundary for every suggested pattern below - a plain
# \s+ isn't enough since providers commonly pipe/dash/colon/paren-delimit fields instead of (or
# as well as) spacing them ("18-09-2026 | 05:00 (GMT)", "NEXT | Team A vs Team B | ...").
_BOUNDARY = r"[\s\-:|()]+"


@dataclass
class RuleSuggestion:
    pattern: str
    start: datetime
    title: str
    # Exact substrings of the sample name this suggestion was built from - either what
    # auto-detection found (suggest_rule_pattern) or the hints the admin supplied
    # (suggest_rule_from_hints, echoed straight back). The API hands these back to the frontend
    # so it can populate the Title/Date/Time hint fields after every Suggest click, letting the
    # admin see and edit exactly what the pattern was built from instead of typing it blind.
    title_hint: str | None = None
    date_hint: str | None = None
    time_hint: str | None = None


def suggest_rule_pattern(sample_name: str, now: datetime | None = None, tz: tzinfo_type = timezone.utc) -> RuleSuggestion | None:
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
    # Same permissive boundary as the title separator below - providers commonly pipe/dash-
    # delimit fields ("18-09-2026 | 05:00 (GMT)"), not just space them, so a plain \s+ here
    # would fail to match the very sample name this rule is being built from.
    joined = _BOUNDARY.join(fragments)

    prefix = sample_name[:match_start].strip(" -|:()")
    suffix = sample_name[match_end:].strip(" -|:()")
    if prefix:
        # A permissive boundary (not just \s+) so a connective dash/colon/pipe/paren right
        # before the date/time - "UFC 300 - 15-09-2026...", "Title 2026 (2026-08-30
        # 20:50:29)" - separates from the title instead of being swallowed into it.
        pattern = rf"(?P<title>.+?){_BOUNDARY}{joined}"
    elif suffix:
        pattern = rf"{joined}{_BOUNDARY}(?P<title>.+)"
    else:
        pattern = joined

    try:
        compiled = validate_rule_pattern(pattern)
    except ValueError:
        return None
    parsed = _apply_custom_rule(sample_name, compiled, now, tz)
    if parsed is None:
        return None
    start, title = parsed
    return RuleSuggestion(
        pattern=pattern,
        start=start,
        title=title,
        title_hint=prefix or suffix or None,
        date_hint=sample_name[date_match.start() : date_match.end()].strip() if date_match else None,
        # TIME_RE's trailing \s* (there to let its optional ampm group follow a space) means the
        # raw match can carry a trailing space when there's no am/pm - trimmed here since this is
        # just for display/re-use as a hint, not the boundary math above that needs the raw span.
        time_hint=sample_name[time_match.start() : time_match.end()].strip(),
    )


_WEEKDAY_PREFIXES = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _generalize_date_hint(date_hint: str) -> str | None:
    """Turns a ground-truth example of a date substring (e.g. "Sat 26 Sep", copy-pasted by the
    admin straight out of a real channel name) into a general regex fragment matching that same
    shape - not a literal copy of this one date, so it also matches sibling channels with
    different weekdays/days/months. The built-in DATE_RE/_SUGGEST_DATE_RE above only understand
    numeric dates ("26/09"); this handles written weekday/month names too, which providers
    commonly use and which auto-detection alone has no way to recognize as a date at all.
    Requires day and month to both be present in the hint (year is optional) - anything less
    isn't enough to compute an actual date, so this returns None."""
    tokens = re.findall(r"[A-Za-z]+|\d+|\s+|[^\sA-Za-z\d]+", date_hint)
    parts: list[str] = []
    have_day = have_month = have_year = False
    for tok in tokens:
        if tok.isspace():
            parts.append(r"\s+")
        elif tok.isalpha():
            low3 = tok.lower()[:3]
            if low3 in _WEEKDAY_PREFIXES:
                parts.append(rf"[A-Za-z]{{{len(tok)}}}")
            elif low3 in _MONTH_NUMBERS and not have_month:
                parts.append(rf"(?P<month>[A-Za-z]{{{len(tok)}}})")
                have_month = True
            else:
                parts.append(re.escape(tok))
        elif tok.isdigit():
            if len(tok) == 4 and not have_year:
                parts.append(r"(?P<year>\d{4})")
                have_year = True
            elif not have_day:
                parts.append(r"(?P<day>\d{1,2})")
                have_day = True
            elif not have_month:
                parts.append(r"(?P<month>\d{1,2})")
                have_month = True
            elif not have_year:
                parts.append(r"(?P<year>\d{2,4})")
                have_year = True
            else:
                parts.append(re.escape(tok))
        else:
            parts.append(re.escape(tok))
    if not (have_day and have_month):
        return None
    return "".join(parts)


def _generalize_gap(text: str) -> str:
    """Turns an arbitrary noise substring sitting between two known anchors (the very start of
    the name and the title, the title and the date/time, or vice versa) into a bounded-length
    regex fragment matching that same shape - e.g. "ON NOW | " becomes
    [A-Za-z]{2}\\s+[A-Za-z]{3}\\s+\\|\\s+ - so it also matches a sibling name whose corresponding
    gap holds different (or no) text of the same shape, the same idea _generalize_date_hint above
    already uses for a weekday/month token.

    Deliberately NOT an open-ended `.*?` wildcard skip: that reads as safe (re.search "skips
    what it doesn't need"), but it's not - a lazy `.+?` title group sitting next to a lazy/
    optional `.*?` skip is genuinely ambiguous whenever the title itself also contains a
    boundary character (almost every multi-word title does, via its own spaces). The engine is
    free to satisfy the pattern by making the skip swallow part of the *title* instead of the
    intended noise - handing back a truncated title - since both readings are equally valid
    matches and it isn't told which one is "right". A bounded shape like [A-Za-z]{2} can only
    ever consume exactly that many characters, so it can't accidentally eat into the title
    alongside it - removing the ambiguity instead of hoping backtracking resolves it correctly.
    Returns "" for an empty gap (nothing to skip)."""
    if not text:
        return ""
    tokens = re.findall(r"[A-Za-z]+|\d+|\s+|[^\sA-Za-z\d]+", text)
    parts: list[str] = []
    for tok in tokens:
        if tok.isspace():
            parts.append(r"\s+")
        elif tok.isalpha():
            parts.append(rf"[A-Za-z]{{{len(tok)}}}")
        elif tok.isdigit():
            parts.append(rf"\d{{{len(tok)}}}")
        else:
            parts.append(re.escape(tok))
    return "".join(parts)


def _generalize_time_hint(time_hint: str) -> str | None:
    """Same idea as _generalize_date_hint, for a ground-truth time substring (e.g. "05:00") -
    reuses TIME_RE (the same shape the built-in parser and suggest_rule_pattern above already
    understand) rather than reinventing time detection."""
    m = TIME_RE.search(time_hint)
    if not m:
        return None
    fragment = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    if m.group("ampm"):
        fragment += r"\s*(?P<ampm>[AaPp]\.?[Mm]\.?)"
    return fragment


def suggest_rule_from_hints(
    sample_name: str,
    title_hint: str | None,
    date_hint: str | None,
    time_hint: str | None,
    now: datetime | None = None,
    tz: tzinfo_type = timezone.utc,
) -> RuleSuggestion | None:
    """Like suggest_rule_pattern, but told exactly which substrings of `sample_name` are the
    title/date/time instead of guessing - for names auto-detection can't handle at all (a
    written month name, or a title that sits in the middle of the string surrounded by unrelated
    noise on both sides, e.g. "NEXT | Team A vs Team B | Sat 26 Sep 05:00 EDT (US) | 8K EXCLUSIVE
    | US: Channel PPV 11"). The date/time fragments are specific enough patterns that re.search()
    naturally skips over anything before or after them on its own - but the title group is just a
    `.+`/`.+?` wildcard, which does *not* skip anything by itself: left alone it happily swallows
    noise like "NEXT |" or "| 8K EXCLUSIVE" right into the title, ignoring where title_hint
    actually said the title starts/ends. So when the hint reveals there's noise on the title's own
    open side (nothing between it and the very start/end of the name), that noise is explicitly
    made skippable in the pattern too, not left for the wildcard to "figure out" - it can't.

    Requires date_hint and time_hint to each be an exact substring of sample_name (copy-pasted,
    not retyped) - title_hint is optional, same as the plain auto-detect suggester. Returns None
    if a hint can't be found in the name or can't be generalized into a date/time shape."""
    now = now or datetime.now(timezone.utc)
    if not date_hint or not time_hint:
        return None

    date_start = sample_name.find(date_hint)
    time_start = sample_name.find(time_hint)
    if date_start == -1 or time_start == -1:
        return None
    date_span = (date_start, date_start + len(date_hint))
    time_span = (time_start, time_start + len(time_hint))

    date_fragment = _generalize_date_hint(date_hint)
    time_fragment = _generalize_time_hint(time_hint)
    if date_fragment is None or time_fragment is None:
        return None

    if date_span[0] <= time_span[0]:
        dt_fragment = date_fragment + _BOUNDARY + time_fragment
    else:
        dt_fragment = time_fragment + _BOUNDARY + date_fragment
    dt_start, dt_end = min(date_span[0], time_span[0]), max(date_span[1], time_span[1])

    title_start = sample_name.find(title_hint) if title_hint else -1
    if title_start != -1:
        title_span = (title_start, title_start + len(title_hint))
        if title_span[1] <= dt_start:
            # Title before the date/time. Any noise between the very start of the name and the
            # title (a provider tag like "NEXT | ") or between the title and the date/time (e.g.
            # "| all |") has to be explicitly made skippable via _generalize_gap - not an
            # open-ended `.*?`, which is genuinely ambiguous whenever the title itself contains a
            # boundary character too (nearly every multi-word title does) and can end up eating
            # part of the title instead of the noise around it (see _generalize_gap's docstring).
            prefix_gap = _generalize_gap(sample_name[: title_span[0]])
            middle_gap = _generalize_gap(sample_name[title_span[1] : dt_start]) or _BOUNDARY
            pattern = rf"{prefix_gap}(?P<title>.+?){middle_gap}{dt_fragment}"
        elif title_span[0] >= dt_end:
            # Mirrored for the date/time-then-title layout: noise between the date/time and the
            # title, and after the title to the end of the name, get the same bounded treatment.
            leading_gap = _generalize_gap(sample_name[dt_end : title_span[0]]) or _BOUNDARY
            suffix_gap = _generalize_gap(sample_name[title_span[1] :])
            title_group = r"(?P<title>.+?)" if suffix_gap else r"(?P<title>.+)"
            anchor = "$" if suffix_gap else ""
            pattern = rf"{dt_fragment}{leading_gap}{title_group}{suffix_gap}{anchor}"
        else:
            # Title hint overlaps the date/time span - not a sane layout to build a boundary
            # from, so fall back to letting the parser strip the matched date/time out of
            # whatever's left, same as when no title hint is given at all.
            pattern = dt_fragment
    else:
        pattern = dt_fragment

    try:
        compiled = validate_rule_pattern(pattern)
    except ValueError:
        return None
    parsed = _apply_custom_rule(sample_name, compiled, now, tz)
    if parsed is None:
        return None
    start, title = parsed
    return RuleSuggestion(
        pattern=pattern, start=start, title=title, title_hint=title_hint, date_hint=date_hint, time_hint=time_hint
    )


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
