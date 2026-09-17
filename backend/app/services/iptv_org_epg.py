import asyncio
import csv
import io
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger("dptv.iptv_org_epg")

DATABASE_BASE_URL = "https://raw.githubusercontent.com/iptv-org/database/refs/heads/master/data"

# --------------------------------------------------------------------------------------
# Reference data (countries.csv / categories.csv / channels.csv / logos.csv), fetched from
# the iptv-org/database repo - used only for the automatic channel logo lookup below. This is
# independent of iptv-org/epg (the scraper project) entirely: no Node.js, no vendored checkout,
# just three small CSV fetches.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelRef:
    id: str
    name: str
    alt_names: tuple[str, ...]
    country_code: str | None
    categories: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceData:
    countries_by_code: dict[str, str]
    categories_by_id: dict[str, str]
    channels_by_id: dict[str, ChannelRef]
    logos_by_channel_id: dict[str, str]


_reference_cache: ReferenceData | None = None
_reference_cache_at: float = 0.0
_reference_lock = asyncio.Lock()
_REFERENCE_CACHE_TTL_SECONDS = 24 * 3600

# Populated by refresh_logo_cache() (called on startup and daily by the scheduler) so that
# request-hot paths (M3U/XMLTV/XC API output) can look up a logo synchronously, with zero
# network I/O and a safe "no logo yet" fallback before the first refresh completes.
_logo_cache: dict[str, str] = {}


def _parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


async def _fetch_csv(client: httpx.AsyncClient, filename: str) -> list[dict[str, str]]:
    resp = await client.get(f"{DATABASE_BASE_URL}/{filename}")
    resp.raise_for_status()
    return _parse_csv(resp.text)


def _pick_best_logo(rows: list[dict[str, str]]) -> str | None:
    """Multiple logo candidates can exist per channel (different feeds, formats, or stale
    entries) - prefer the channel-level default (no feed) over a specific feed's logo, prefer
    one iptv-org's own data marks as currently in_use, and prefer PNG (universally supported)
    over SVG (not renderable by most IPTV players/set-top boxes) or other formats."""

    def score(row: dict[str, str]) -> tuple[int, int, int]:
        return (
            0 if row.get("feed") else 1,
            1 if (row.get("in_use") or "").upper() == "TRUE" else 0,
            2 if (row.get("format") or "").upper() == "PNG" else (1 if (row.get("format") or "").upper() == "SVG" else 0),
        )

    if not rows:
        return None
    best = max(rows, key=score)
    return best.get("url") or None


async def _load_reference_data() -> ReferenceData:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        countries_rows, categories_rows, channels_rows, logos_rows = await asyncio.gather(
            _fetch_csv(client, "countries.csv"),
            _fetch_csv(client, "categories.csv"),
            _fetch_csv(client, "channels.csv"),
            _fetch_csv(client, "logos.csv"),
        )

    countries_by_code = {row["code"]: row["name"] for row in countries_rows if row.get("code")}
    categories_by_id = {row["id"]: row["name"] for row in categories_rows if row.get("id")}

    channels_by_id: dict[str, ChannelRef] = {}
    for row in channels_rows:
        cid = row.get("id")
        if not cid:
            continue
        categories = tuple(c for c in (row.get("categories") or "").split(";") if c)
        alt_names = tuple(n for n in (row.get("alt_names") or "").split(";") if n)
        channels_by_id[cid] = ChannelRef(
            id=cid, name=row.get("name") or cid, alt_names=alt_names,
            country_code=row.get("country") or None, categories=categories,
        )

    logos_by_id_rows: dict[str, list[dict[str, str]]] = {}
    for row in logos_rows:
        cid = row.get("channel")
        if not cid:
            continue
        logos_by_id_rows.setdefault(cid, []).append(row)
    logos_by_channel_id = {cid: url for cid, rows in logos_by_id_rows.items() if (url := _pick_best_logo(rows))}

    return ReferenceData(
        countries_by_code=countries_by_code,
        categories_by_id=categories_by_id,
        channels_by_id=channels_by_id,
        logos_by_channel_id=logos_by_channel_id,
    )


async def get_reference_data(force_refresh: bool = False) -> ReferenceData:
    global _reference_cache, _reference_cache_at
    async with _reference_lock:
        stale = _reference_cache is None or (time.monotonic() - _reference_cache_at) > _REFERENCE_CACHE_TTL_SECONDS
        if force_refresh or stale:
            _reference_cache = await _load_reference_data()
            _reference_cache_at = time.monotonic()
        return _reference_cache


def strip_feed_suffix(xmltv_id: str) -> str:
    """A channel id can carry the form '{base_channel_id}@{feed_id}' (e.g.
    'PlutoTV80sAction.us@CA'), referencing a specific regional/feed variant. logos.csv keys on
    the bare base id, so this must be stripped before lookup."""
    return xmltv_id.split("@", 1)[0]


async def refresh_logo_cache() -> int:
    """Refreshes the in-memory synchronous logo lookup cache."""
    global _logo_cache
    ref = await get_reference_data(force_refresh=True)
    _logo_cache = dict(ref.logos_by_channel_id)
    logger.info("Refreshed iptv-org logo cache: %d channel logos", len(_logo_cache))
    return len(_logo_cache)


def get_cached_logo_url(epg_channel_id: str | None) -> str | None:
    """Synchronous, zero-I/O lookup for use on request-hot paths (M3U/XMLTV/XC API output).
    Returns None (never raises, never blocks) if the cache hasn't been populated yet or the
    id isn't recognized - callers should treat this as just another optional fallback."""
    if not epg_channel_id or not _logo_cache:
        return None
    return _logo_cache.get(strip_feed_suffix(epg_channel_id))
