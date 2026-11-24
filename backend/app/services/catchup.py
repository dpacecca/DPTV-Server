"""(1.0.23) Provider catch-up passed through to players.

A player asks DPTV-Server for a past programme the Xtream way,
`/timeshift/{user}/{pass}/{minutes}/{yyyy-MM-dd:HH-mm}/{stream}.ts`, with the start in UTC (the
time zone DPTV-Server's own server_info names). The provider's panel reads that start in its own
time zone, so the start is moved into it before the player is sent on.
"""

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.source import Source

log = logging.getLogger(__name__)

START_FORMAT = "%Y-%m-%d:%H-%M"
_ZONE_TTL_S = 6 * 60 * 60
_zones: dict[tuple[str, str], tuple[float, str]] = {}


def parse_start(text: str) -> datetime | None:
    """A timeshift start, "2026-11-24:19-30" (or with ":" between hour and minute), as UTC."""
    t = (text or "").strip()
    for fmt in (START_FORMAT, "%Y-%m-%d:%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def zone(name: str | None):
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def timeshift_url(base_url: str, username: str, password: str, minutes: int, start_utc: datetime,
                  stream_id: str, ext: str, zone_name: str | None) -> str:
    start = start_utc.astimezone(zone(zone_name)).strftime(START_FORMAT)
    return f"{base_url.strip().rstrip('/')}/timeshift/{username}/{password}/{minutes}/{start}/{stream_id}.{ext}"


async def provider_zone(source: Source) -> str | None:
    """The panel's own time zone from its server_info, kept for six hours; None when it can't say
    (the start then stays in UTC, which is what most panels run on)."""
    key = (source.base_url.strip().lower(), source.username)
    hit = _zones.get(key)
    if hit and time.monotonic() - hit[0] < _ZONE_TTL_S:
        return hit[1] or None
    from app.services.xtream_client import XtreamClient

    name = ""
    try:
        data = await XtreamClient(source).authenticate()
        name = str((data.get("server_info") or {}).get("timezone") or "").strip()
    except Exception as exc:  # the panel's answer only refines the start; never fail catch-up on it
        log.info("catch-up: panel time zone unavailable for source %s (%s)", source.id, type(exc).__name__)
    # A failed lookup is asked again after five minutes, not six hours.
    _zones[key] = (time.monotonic() - (0 if name else _ZONE_TTL_S - 300), name)
    return name or None


def forget_zones() -> None:
    _zones.clear()
