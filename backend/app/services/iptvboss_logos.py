import asyncio
import logging
import os
import re
from urllib.parse import quote, unquote

import httpx
from lxml import html as lxml_html

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

# Rebuilt wholesale by refresh_iptvboss_logo_cache() (startup + weekly, see core/scheduler.py) -
# maps a normalize_name()'d channel name to the logo's path under the local cache directory, so
# request-hot paths (M3U/XMLTV/XC API output) can resolve a logo synchronously with zero network
# I/O. Empty (not an error) until the first refresh completes.
_name_index: dict[str, str] = {}


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
    for status, rel_path in results:
        stats[status] += 1
        if status == "error":
            continue
        filename = rel_path.rsplit("/", 1)[-1]
        stem = os.path.splitext(filename)[0]
        key = normalize_name(_strip_country_suffix(stem))
        if key and key not in index:
            index[key] = rel_path

    global _name_index
    _name_index = index

    logger.info(
        "Refreshed iptvboss logo cache: %d files found, %d downloaded, %d skipped, %d errors, %d channel names indexed",
        len(files), stats["downloaded"], stats["skipped"], stats["error"], len(index),
    )
    return {"files_found": len(files), **stats, "channels_indexed": len(index)}


def get_iptvboss_logo_url(channel_name: str | None) -> str | None:
    """Synchronous, zero-I/O lookup for use on request-hot paths. Matches purely on channel
    name (normalize_name()'d, same as EPG fuzzy matching) against the locally-cached iptvboss
    set - unlike the iptv-org fallback in iptv_org_epg.py, this never needs an EPG mapping at
    all, so it also covers channels with no EPG match. Returns an absolute URL served by this
    server itself (see the /logo-cache static mount in main.py), never the remote iptvboss URL -
    the whole point is this app doesn't depend on that CDN being reachable at request time."""
    if not channel_name or not _name_index:
        return None
    rel_path = _name_index.get(normalize_name(channel_name))
    if not rel_path:
        return None
    settings = get_settings()
    quoted = "/".join(quote(part) for part in rel_path.split("/"))
    return f"{settings.public_base_url.rstrip('/')}/logo-cache/{quoted}"
