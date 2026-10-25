import asyncio
import logging
import os
import re
from urllib.parse import quote, unquote

import httpx
from lxml import html as lxml_html
from rapidfuzz import fuzz, process

from app.config import get_settings
from app.services.name_normalize import normalize_name

logger = logging.getLogger("dptv.iptvboss_logos")

BASE_URL = "https://cdn.iptvboss.pro/logos/"

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# The index is several levels deep in places (country -> optional subcategory) but never
# pathological - bounded mainly so a malformed/unexpected listing can't send this into an
# unbounded crawl.
_MAX_DEPTH = 4
_LISTING_CONCURRENCY = 8
_DOWNLOAD_CONCURRENCY = 8

_COUNTRY_SUFFIX_RE = re.compile(r"^(.+)\.([A-Za-z]{2,3})$")

# Same similarity threshold epg_mapper.auto_match defaults to, for the same reason: below this,
# WRatio starts pairing genuinely different channels together often enough that a wrong guess is
# worse than no logo at all.
_FUZZY_SENSITIVITY = 0.9

# All three rebuilt wholesale by refresh_iptvboss_logo_cache() (startup + weekly, see
# core/scheduler.py) - empty (not an error) until the first refresh completes.
# normalize_name()'d channel name -> logo path under the local cache directory. The fast path:
# request-hot callers (M3U/XMLTV/XC API output) hit this first, zero network I/O and no scoring.
_name_index: dict[str, str] = {}
# logo path -> its normalize_name()'d channel name - the candidate pool get_iptvboss_logo_url()
# falls back to fuzzy-matching against (same rapidfuzz WRatio approach as epg_mapper.
# search_candidates) when _name_index has no exact entry for a channel's name.
_fuzzy_choices: dict[str, str] = {}
# Memoizes a normalized channel name -> logo path (or None for "no confident match") once
# _fuzzy_match has resolved it, so repeat lookups for the same channel - M3U/XMLTV output
# re-resolves every channel's logo on every request - don't re-run a fuzzy search each time.
_fuzzy_cache: dict[str, str | None] = {}
# Every cached logo, undeduplicated (unlike _name_index/_fuzzy_choices, which keep only one
# country's file per channel name) - (display name, logo path) pairs backing search_logos(), so
# an admin manually picking a logo can see and choose between e.g. the US and UK "ESPN" rather
# than being stuck with whichever one auto-matching happened to pick.
_all_logos: list[tuple[str, str]] = []


def _cache_root() -> str:
    return os.path.join(get_settings().data_dir, "logo_cache")


def _parse_listing(text: str) -> list[tuple[str, bool, int | None]]:
    """Parses an Apache-style "Index of /..." directory listing. Returns (href, is_dir, size)
    per entry - href as given by the server (already URL-escaped, safe to append to a request
    URL directly), size is None for directories and for any row this couldn't parse a trailing
    byte count out of (the listing's last whitespace-separated column on that row)."""
    tree = lxml_html.fromstring(text)
    entries: list[tuple[str, bool, int | None]] = []
    for a in tree.xpath("//a"):
        href = a.get("href") or ""
        if not href or href in ("../", "./") or href.startswith(("?", "/", "http://", "https://")):
            continue
        is_dir = href.endswith("/")
        size = None
        if not is_dir:
            tail_parts = (a.tail or "").split()
            if tail_parts and tail_parts[-1].isdigit():
                size = int(tail_parts[-1])
        entries.append((href, is_dir, size))
    return entries


def _strip_country_suffix(stem: str) -> str:
    """"21Junior.al" -> "21Junior" - iptvboss names logos "{ChannelName}.{country}.{ext}",
    mirroring iptv-org's own "Name.cc" convention (see services/iptv_org_epg.py)."""
    m = _COUNTRY_SUFFIX_RE.match(stem)
    return m.group(1) if m else stem


async def _crawl(client: httpx.AsyncClient) -> list[tuple[str, int]]:
    """Walks the whole /logos/ tree. Returns (clean_relative_path, size_bytes) for every image
    file found - clean_relative_path is unquoted (safe to use as a local filesystem path and for
    name matching), joined with "/" regardless of platform."""
    sem = asyncio.Semaphore(_LISTING_CONCURRENCY)
    found: list[tuple[str, int]] = []

    async def walk(href_path: str, clean_path: str, depth: int) -> None:
        async with sem:
            resp = await client.get(BASE_URL + href_path)
            resp.raise_for_status()
        subtasks = []
        for href, is_dir, size in _parse_listing(resp.text):
            name = unquote(href[:-1] if is_dir else href)
            child_href = href_path + href
            child_clean = f"{clean_path}{name}/" if is_dir else f"{clean_path}{name}"
            if is_dir:
                if depth < _MAX_DEPTH:
                    subtasks.append(walk(child_href, child_clean, depth + 1))
            else:
                if os.path.splitext(name)[1].lower() in _IMAGE_EXTS:
                    found.append((child_clean, size or 0))
        if subtasks:
            await asyncio.gather(*subtasks)

    await walk("", "", 0)
    return found


def _safe_local_path(rel_path: str) -> str | None:
    parts = [p for p in rel_path.split("/") if p and p not in (".", "..")]
    if not parts:
        return None
    return os.path.join(_cache_root(), *parts)


async def _download_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, rel_path: str, remote_size: int) -> tuple[str, str]:
    """Returns (status, rel_path) with status one of "downloaded"/"skipped"/"error". Skips
    (cheaply, no request) a file already on disk at the same size the listing just reported -
    this is what makes the weekly rescan an incremental update rather than a full re-download."""
    local_path = _safe_local_path(rel_path)
    if local_path is None:
        return "error", rel_path
    if os.path.exists(local_path) and (remote_size <= 0 or os.path.getsize(local_path) == remote_size):
        return "skipped", rel_path
    async with sem:
        try:
            href = "/".join(quote(part) for part in rel_path.split("/"))
            resp = await client.get(BASE_URL + href)
            resp.raise_for_status()
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            tmp_path = f"{local_path}.part"
            with open(tmp_path, "wb") as f:
                f.write(resp.content)
            os.replace(tmp_path, local_path)
            return "downloaded", rel_path
        except Exception:  # noqa: BLE001 - one bad file should never abort the whole refresh
            logger.warning("Failed to download logo %r", rel_path, exc_info=True)
            return "error", rel_path


async def refresh_iptvboss_logo_cache() -> dict:
    """Rescans the whole iptvboss logo tree, downloads anything new or changed (see
    _download_one), and rebuilds the name index from everything that's now actually present on
    disk. Called on startup and weekly by the scheduler (see core/scheduler.py)."""
    os.makedirs(_cache_root(), exist_ok=True)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        files = await _crawl(client)
        download_sem = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)
        results = await asyncio.gather(*(_download_one(client, download_sem, rel, size) for rel, size in files))

    stats = {"downloaded": 0, "skipped": 0, "error": 0}
    index: dict[str, str] = {}
    all_logos: list[tuple[str, str]] = []
    for status, rel_path in results:
        stats[status] += 1
        if status == "error":
            continue
        filename = rel_path.rsplit("/", 1)[-1]
        stem = os.path.splitext(filename)[0]
        base_name = _strip_country_suffix(stem)
        country = stem[len(base_name) + 1:] if len(stem) > len(base_name) else None
        display_name = f"{base_name} ({country.upper()})" if country else base_name
        all_logos.append((display_name, rel_path))
        key = normalize_name(base_name)
        if key and key not in index:
            index[key] = rel_path

    global _name_index, _fuzzy_choices, _fuzzy_cache, _all_logos
    _name_index = index
    _fuzzy_choices = {rel_path: name for name, rel_path in index.items()}
    _fuzzy_cache = {}
    _all_logos = all_logos

    logger.info(
        "Refreshed iptvboss logo cache: %d files found, %d downloaded, %d skipped, %d errors, %d channel names indexed",
        len(files), stats["downloaded"], stats["skipped"], stats["error"], len(index),
    )
    return {"files_found": len(files), **stats, "channels_indexed": len(index)}


def _fuzzy_match(key: str) -> str | None:
    if key in _fuzzy_cache:
        return _fuzzy_cache[key]
    result = process.extractOne(key, _fuzzy_choices, scorer=fuzz.WRatio, score_cutoff=_FUZZY_SENSITIVITY * 100)
    rel_path = result[2] if result else None
    _fuzzy_cache[key] = rel_path
    return rel_path


def _logo_url(rel_path: str) -> str:
    settings = get_settings()
    quoted = "/".join(quote(part) for part in rel_path.split("/"))
    return f"{settings.public_base_url.rstrip('/')}/logo-cache/{quoted}"


def get_iptvboss_logo_url(channel_name: str | None) -> str | None:
    """Synchronous, zero-I/O lookup for use on request-hot paths. Matches on channel name
    (normalize_name()'d, same as EPG fuzzy matching) against the locally-cached iptvboss set -
    an exact normalized match first, then the same rapidfuzz WRatio approach epg_mapper uses for
    EPG matching if that misses (see _fuzzy_match). Unlike the iptv-org fallback in
    iptv_org_epg.py, this never needs an EPG mapping at all, so it also covers channels with no
    EPG match. Returns an absolute URL served by this server itself (see the /logo-cache static
    mount in main.py), never the remote iptvboss URL - the whole point is this app doesn't depend
    on that CDN being reachable at request time."""
    if not channel_name or not _name_index:
        return None
    key = normalize_name(channel_name)
    rel_path = _name_index.get(key) or _fuzzy_match(key)
    if not rel_path:
        return None
    return _logo_url(rel_path)


def search_logos(query: str, limit: int = 20) -> list[dict]:
    """Lets an admin search the whole cached logo set by name (e.g. to manually pick a better
    match than get_iptvboss_logo_url() found, or find one for a channel it couldn't match at
    all) - for the "Channel Settings" logo override field's search picker. Ranked with the same
    rapidfuzz WRatio scoring as the automatic match, but against every cached logo rather than
    the deduplicated one-per-channel-name index, so e.g. both the US and UK "ESPN" show up as
    separate results to choose between rather than whichever one auto-matching picked."""
    query = query.strip()
    if not query or not _all_logos:
        return []
    q = normalize_name(query)
    choices = {i: normalize_name(name) for i, (name, _rel_path) in enumerate(_all_logos)}
    results = process.extract(q, choices, scorer=fuzz.WRatio, limit=limit, score_cutoff=40.0)
    return [
        {"name": _all_logos[idx][0], "url": _logo_url(_all_logos[idx][1]), "score": round(score / 100, 3)}
        for _text, score, idx in results
    ]
