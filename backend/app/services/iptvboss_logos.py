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

_COUNTRY_SUFFIX_RE = re.compile(r"^(.+)\.([A-Za-z]{2,3})$")

# Same similarity threshold epg_mapper.auto_match defaults to, for the same reason: below this,
# WRatio starts pairing genuinely different channels together often enough that a wrong guess is
# worse than no logo at all.
_FUZZY_SENSITIVITY = 0.9

# Nothing here is ever downloaded or written to disk - this app hotlinks iptvboss's own CDN
# directly rather than mirroring it (an earlier version downloaded everything locally; on a real
# install that meant 20GB+ of someone else's entire logo catalog, most of it for channels nobody
# actually had, competing with replicated/HA-sensitive storage for no real benefit). All four of
# these are rebuilt wholesale by refresh_iptvboss_logo_index() (startup + weekly, see
# core/scheduler.py) from the directory listing alone - empty (not an error) until the first
# refresh completes.
# normalize_name()'d channel name -> that logo's iptvboss CDN URL. The fast path: request-hot
# callers (M3U/XMLTV/XC API output) hit this first, zero network I/O and no scoring.
_name_index: dict[str, str] = {}
# CDN URL -> its normalize_name()'d channel name - the candidate pool get_iptvboss_logo_url()
# falls back to fuzzy-matching against (same rapidfuzz WRatio approach as epg_mapper.
# search_candidates) when _name_index has no exact entry for a channel's name.
_fuzzy_choices: dict[str, str] = {}
# Memoizes a normalized channel name -> CDN URL (or None for "no confident match") once
# _fuzzy_match has resolved it, so repeat lookups for the same channel - M3U/XMLTV output
# re-resolves every channel's logo on every request - don't re-run a fuzzy search each time.
_fuzzy_cache: dict[str, str | None] = {}
# Every logo in the catalog, undeduplicated (unlike _name_index/_fuzzy_choices, which keep only
# one country's entry per channel name) - (display name, CDN URL) pairs backing search_logos(),
# so an admin manually picking a logo can see and choose between e.g. the US and UK "ESPN" rather
# than being stuck with whichever one auto-matching happened to pick.
_all_logos: list[tuple[str, str]] = []


def _parse_listing(text: str) -> list[tuple[str, bool, int | None]]:
    """Parses an Apache-style "Index of /..." directory listing. Returns (href, is_dir, size)
    per entry - href as given by the server (already URL-escaped, safe to append to a request
    URL directly), size is None for directories and for any row this couldn't parse a trailing
    byte count out of (the listing's last whitespace-separated column on that row). The size is
    unused now (nothing is downloaded) but costs nothing extra to keep parsing."""
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


async def _crawl(client: httpx.AsyncClient) -> list[str]:
    """Walks the whole /logos/ tree, reading only the directory listing pages themselves (never
    an actual image). Returns the href path (URL-escaped, ready to append to BASE_URL) of every
    image file found."""
    sem = asyncio.Semaphore(_LISTING_CONCURRENCY)
    found: list[str] = []

    async def walk(href_path: str, depth: int) -> None:
        async with sem:
            resp = await client.get(BASE_URL + href_path)
            resp.raise_for_status()
        subtasks = []
        for href, is_dir, _size in _parse_listing(resp.text):
            child_href = href_path + href
            if is_dir:
                if depth < _MAX_DEPTH:
                    subtasks.append(walk(child_href, depth + 1))
            elif os.path.splitext(unquote(href))[1].lower() in _IMAGE_EXTS:
                found.append(child_href)
        if subtasks:
            await asyncio.gather(*subtasks)

    await walk("", 0)
    return found


async def refresh_iptvboss_logo_index() -> dict:
    """Re-crawls the iptvboss directory listing (text only, no image downloads - see _crawl) and
    rebuilds the name index from it. Called on startup and weekly by the scheduler (see
    core/scheduler.py); cheap enough to run often since it never touches disk or downloads image
    bytes, only the listing pages."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        hrefs = await _crawl(client)

    index: dict[str, str] = {}
    all_logos: list[tuple[str, str]] = []
    for href in hrefs:
        filename = unquote(href.rsplit("/", 1)[-1])
        stem = os.path.splitext(filename)[0]
        base_name = _strip_country_suffix(stem)
        country = stem[len(base_name) + 1:] if len(stem) > len(base_name) else None
        display_name = f"{base_name} ({country.upper()})" if country else base_name
        url = BASE_URL + href
        all_logos.append((display_name, url))
        key = normalize_name(base_name)
        if key and key not in index:
            index[key] = url

    global _name_index, _fuzzy_choices, _fuzzy_cache, _all_logos
    _name_index = index
    _fuzzy_choices = {url: name for name, url in index.items()}
    _fuzzy_cache = {}
    _all_logos = all_logos

    logger.info("Refreshed iptvboss logo index: %d logos found, %d channel names indexed", len(hrefs), len(index))
    return {"logos_found": len(hrefs), "channels_indexed": len(index)}


def _fuzzy_match(key: str) -> str | None:
    if key in _fuzzy_cache:
        return _fuzzy_cache[key]
    result = process.extractOne(key, _fuzzy_choices, scorer=fuzz.WRatio, score_cutoff=_FUZZY_SENSITIVITY * 100)
    url = result[2] if result else None
    _fuzzy_cache[key] = url
    return url


def get_iptvboss_logo_url(channel_name: str | None) -> str | None:
    """Synchronous, zero-I/O lookup for use on request-hot paths. Matches on channel name
    (normalize_name()'d, same as EPG fuzzy matching) against the iptvboss catalog - an exact
    normalized match first, then the same rapidfuzz WRatio approach epg_mapper uses for EPG
    matching if that misses (see _fuzzy_match). Unlike the iptv-org fallback in iptv_org_epg.py,
    this never needs an EPG mapping at all, so it also covers channels with no EPG match. Returns
    iptvboss's own CDN URL directly - this app hotlinks it rather than mirroring it locally (see
    the module docstring comment above _name_index)."""
    if not channel_name or not _name_index:
        return None
    key = normalize_name(channel_name)
    return _name_index.get(key) or _fuzzy_match(key)


def search_logos(query: str, limit: int = 20) -> list[dict]:
    """Lets an admin search the whole iptvboss catalog by name (e.g. to manually pick a better
    match than get_iptvboss_logo_url() found, or find one for a channel it couldn't match at
    all) - for the "Channel Settings" logo override field's search picker. Ranked with the same
    rapidfuzz WRatio scoring as the automatic match, but against every logo in the catalog rather
    than the deduplicated one-per-channel-name index, so e.g. both the US and UK "ESPN" show up
    as separate results to choose between rather than whichever one auto-matching picked. Results
    carry iptvboss's own CDN URL directly (see get_iptvboss_logo_url)."""
    query = query.strip()
    if not query or not _all_logos:
        return []
    q = normalize_name(query)
    choices = {i: normalize_name(name) for i, (name, _url) in enumerate(_all_logos)}
    results = process.extract(q, choices, scorer=fuzz.WRatio, limit=limit, score_cutoff=40.0)
    return [
        {"name": _all_logos[idx][0], "url": _all_logos[idx][1], "score": round(score / 100, 3)}
        for _text, score, idx in results
    ]
