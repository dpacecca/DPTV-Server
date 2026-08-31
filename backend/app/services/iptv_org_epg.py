import asyncio
import csv
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx

from app.config import get_settings

logger = logging.getLogger("dptv.iptv_org_epg")

DATABASE_BASE_URL = "https://raw.githubusercontent.com/iptv-org/database/refs/heads/master/data"
INTERNATIONAL = "International / Other"

# --------------------------------------------------------------------------------------
# Reference data (countries.csv / categories.csv / channels.csv / logos.csv), fetched from
# the separate iptv-org/database repo. This is independent of the scraper itself (no Node.js
# or vendored checkout required) - the logo lookup in particular works even when
# `iptv_org_epg_dir` isn't configured, since it's just three small CSV fetches.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelRef:
    id: str
    name: str
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
        channels_by_id[cid] = ChannelRef(
            id=cid, name=row.get("name") or cid, country_code=row.get("country") or None, categories=categories
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
    """A site's xmltv_id has the form '{base_channel_id}@{feed_id}' (e.g.
    'PlutoTV80sAction.us@CA'), referencing a specific regional/feed variant. channels.csv and
    logos.csv key on the bare base id, so this must be stripped before either lookup."""
    return xmltv_id.split("@", 1)[0]


async def refresh_logo_cache() -> int:
    """Refreshes the in-memory synchronous logo lookup cache. Safe to call even when the
    iptv-org/epg scraper itself isn't configured - this only needs logos.csv."""
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


# --------------------------------------------------------------------------------------
# TLD-based country heuristic, used only as a fallback for a channel entry that has no real
# xmltv_id match in channels.csv (iptv-org's own per-site country metadata covers roughly a
# sixth of all listed channel entries - the rest need something rather than nothing).
# --------------------------------------------------------------------------------------

_COMPOUND_CCTLD_COUNTRY = {
    "com.au": "Australia",
    "co.uk": "United Kingdom",
    "com.br": "Brazil",
    "com.tr": "Turkey",
    "com.uy": "Uruguay",
    "com.ar": "Argentina",
    "co.kr": "South Korea",
    "co.il": "Israel",
    "co.jp": "Japan",
    "com.my": "Malaysia",
}

_SIMPLE_TLD_COUNTRY = {
    "ad": "Andorra", "ao": "Angola", "ar": "Argentina", "at": "Austria", "au": "Australia",
    "ba": "Bosnia and Herzegovina", "be": "Belgium", "bg": "Bulgaria", "br": "Brazil",
    "ca": "Canada", "ch": "Switzerland", "cl": "Chile", "cu": "Cuba", "cy": "Cyprus",
    "cz": "Czechia", "de": "Germany", "dk": "Denmark", "ee": "Estonia", "es": "Spain",
    "fi": "Finland", "fj": "Fiji", "fo": "Faroe Islands", "fr": "France", "ge": "Georgia",
    "gl": "Greenland", "gr": "Greece", "hk": "Hong Kong", "hr": "Croatia", "hu": "Hungary",
    "id": "Indonesia", "ie": "Ireland", "il": "Israel", "in": "India", "is": "Iceland",
    "it": "Italy", "jp": "Japan", "kr": "South Korea", "kz": "Kazakhstan", "lk": "Sri Lanka",
    "lt": "Lithuania", "lu": "Luxembourg", "lv": "Latvia", "ma": "Morocco", "mk": "North Macedonia",
    "mn": "Mongolia", "mt": "Malta", "my": "Malaysia", "nl": "Netherlands", "no": "Norway",
    "nz": "New Zealand", "om": "Oman", "pe": "Peru", "pf": "French Polynesia", "pl": "Poland",
    "pt": "Portugal", "ro": "Romania", "rs": "Serbia", "ru": "Russia", "se": "Sweden",
    "sg": "Singapore", "sk": "Slovakia", "th": "Thailand", "tr": "Turkey", "uk": "United Kingdom",
    "us": "United States", "uy": "Uruguay", "vn": "Vietnam", "zm": "Zambia",
}


def infer_country_from_domain(site_domain: str) -> str:
    for suffix, country in _COMPOUND_CCTLD_COUNTRY.items():
        if site_domain.endswith("." + suffix):
            return country
    tld = site_domain.rsplit(".", 1)[-1]
    return _SIMPLE_TLD_COUNTRY.get(tld, INTERNATIONAL)


# --------------------------------------------------------------------------------------
# Site catalog: scans the vendored iptv-org/epg checkout's sites/ directory (no network call)
# and resolves each channel entry's country/categories against the reference data.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GrabChannelEntry:
    site: str
    site_id: str
    lang: str
    xmltv_id: str
    name: str
    country: str
    """Resolved country display name - real data when the entry's xmltv_id matches
    channels.csv, otherwise the site's TLD-heuristic country (never blank)."""
    matched: bool
    """Whether this entry resolved against real channels.csv data (vs. the TLD fallback)."""
    categories: tuple[str, ...]
    """Category ids (channels.csv taxonomy) - only populated for matched entries; there's no
    heuristic fallback for category the way there is for country."""


@dataclass(frozen=True)
class CountryOption:
    name: str
    channel_count: int
    matched_channel_count: int


@dataclass(frozen=True)
class CategoryOption:
    id: str
    name: str
    channel_count: int


def _scan_site_files_sync(sites_dir: Path) -> list[tuple[str, str, str, str, str]]:
    """Returns raw (site, site_id, lang, xmltv_id, name) tuples for every <channel> across
    every sites/*/*.channels.xml file. Pure filesystem + XML parsing, no network - run in a
    thread since it touches ~250 directories."""
    out: list[tuple[str, str, str, str, str]] = []
    if not sites_dir.is_dir():
        return out
    for site_dir in sorted(sites_dir.iterdir()):
        if not site_dir.is_dir():
            continue
        for xml_path in site_dir.glob("*.channels.xml"):
            try:
                tree = ElementTree.parse(xml_path)
            except ElementTree.ParseError:
                continue
            for el in tree.getroot().findall("channel"):
                site = el.get("site") or site_dir.name
                site_id = el.get("site_id") or ""
                lang = el.get("lang") or ""
                xmltv_id = el.get("xmltv_id") or ""
                name = (el.text or "").strip()
                if not site_id or not name:
                    continue
                out.append((site, site_id, lang, xmltv_id, name))
    return out


async def build_grab_entries() -> list[GrabChannelEntry]:
    """The full catalog of every channel the vendored scraper checkout knows how to grab,
    each resolved to a country and (if matched) a set of categories. Returns [] if the
    checkout isn't configured/present."""
    settings = get_settings()
    if not settings.iptv_org_epg_dir:
        return []
    sites_dir = Path(settings.iptv_org_epg_dir) / "sites"

    raw_entries, ref = await asyncio.gather(
        asyncio.to_thread(_scan_site_files_sync, sites_dir),
        get_reference_data(),
    )

    entries: list[GrabChannelEntry] = []
    for site, site_id, lang, xmltv_id, name in raw_entries:
        base_id = strip_feed_suffix(xmltv_id) if xmltv_id else ""
        channel_ref = ref.channels_by_id.get(base_id) if base_id else None
        if channel_ref is not None and channel_ref.country_code in ref.countries_by_code:
            country = ref.countries_by_code[channel_ref.country_code]
            matched = True
            categories = channel_ref.categories
        else:
            country = infer_country_from_domain(site)
            matched = False
            categories = ()
        entries.append(
            GrabChannelEntry(
                site=site, site_id=site_id, lang=lang, xmltv_id=xmltv_id, name=name,
                country=country, matched=matched, categories=categories,
            )
        )
    return entries


async def list_countries() -> list[CountryOption]:
    entries = await build_grab_entries()
    grouped: dict[str, list[GrabChannelEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.country, []).append(entry)

    options = [
        CountryOption(
            name=country,
            channel_count=len(group),
            matched_channel_count=sum(1 for e in group if e.matched),
        )
        for country, group in grouped.items()
    ]
    # International last, everything else alphabetical - matches how an admin would scan the list.
    options.sort(key=lambda c: (c.name == INTERNATIONAL, c.name))
    return options


async def list_categories() -> list[CategoryOption]:
    ref = await get_reference_data()
    entries = await build_grab_entries()
    counts: dict[str, int] = {}
    for entry in entries:
        if not entry.matched:
            continue
        for category_id in entry.categories:
            counts[category_id] = counts.get(category_id, 0) + 1

    options = [
        CategoryOption(id=cid, name=ref.categories_by_id.get(cid, cid), channel_count=count)
        for cid, count in counts.items()
    ]
    options.sort(key=lambda c: c.name)
    return options


async def grab_entries_for_countries(country_names: list[str]) -> list[GrabChannelEntry]:
    wanted = set(country_names)
    entries = await build_grab_entries()
    return [e for e in entries if e.country in wanted]


async def grab_entries_for_categories(category_ids: list[str]) -> list[GrabChannelEntry]:
    wanted = set(category_ids)
    entries = await build_grab_entries()
    return [e for e in entries if wanted.intersection(e.categories)]


def write_channels_xml(entries: list[GrabChannelEntry], path: Path) -> None:
    """Writes the grabber's --channels=<path> input format: a flat <channels> list of
    <channel site=.. site_id=.. lang=..>Name</channel> entries, matching what sites/*/*.channels.xml
    already looks like. Precise channel-level selection instead of --sites=, since selecting
    a whole site would drag in channels from every country/category it happens to carry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<channels>"]
    for e in entries:
        lines.append(
            f'  <channel site="{escape(e.site)}" site_id="{escape(e.site_id)}" lang="{escape(e.lang)}">'
            f"{escape(e.name)}</channel>"
        )
    lines.append("</channels>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run_grab_batch(
    entries: list[GrabChannelEntry], output_path: Path, epg_dir: Path, timeout: float
) -> None:
    """Invokes the vendored iptv-org/epg grabber once, for a single batch of channel entries.

    Deliberately doesn't use the grabber's own --gzip output option - this app's XMLTV parser
    already transparently handles gzip (see epg_parser._maybe_decompress) for URL-based
    sources, so there's nothing to gain from depending on exactly how --gzip names its second
    output file, and reading the one plain --output path this call controls directly is
    simpler to get right."""
    channels_xml_path = output_path.with_suffix(".channels.xml")
    write_channels_xml(entries, channels_xml_path)

    cmd = [
        "npm", "run", "grab", "---",
        f"--channels={channels_xml_path}",
        f"--output={output_path}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(epg_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Grab timed out after {timeout:.0f}s for {len(entries)} channel(s)")
    finally:
        channels_xml_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-2000:] or "grab failed with no output")

    if not output_path.exists():
        raise RuntimeError("Grab completed but no output file was produced")


def _merge_xmltv_batches(batch_paths: list[Path], output_path: Path) -> None:
    """Concatenates each batch's <channel>/<programme> elements into one XMLTV file by
    string-splicing on the outer <tv> tag (the same approach xc_server.get_xmltv already uses
    to combine multiple playlists' guides) rather than parsing everything into memory at
    once - doing that here would undo the whole point of batching the grab in the first
    place."""
    with output_path.open("w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="iptv-org/epg">')
        for batch_path in batch_paths:
            text = batch_path.read_text(encoding="utf-8")
            if "<tv" not in text:
                continue
            inner = text.split("<tv", 1)[1].split(">", 1)[1].rsplit("</tv>", 1)[0]
            out.write(inner)
        out.write("</tv>\n")


async def run_grab(
    entries: list[GrabChannelEntry], output_path: Path, timeout_seconds: float | None = None
) -> None:
    """Scrapes guide data for the given channel entries and writes a combined XMLTV file to
    output_path (an absolute path, since each grabber subprocess runs with the checkout as its
    cwd). Raises RuntimeError with the scraper's own stderr on failure - scraping real
    broadcaster sites is exactly the kind of thing that fails in ways worth surfacing verbatim.

    Large selections are scraped in sequential batches (one grabber subprocess at a time, see
    iptv_org_grab_batch_size) instead of one monolithic run - the grabber holds an entire
    selection's guide in memory until it writes the output at the very end, which is enough to
    OOM a small server on a selection no larger than a single mid-sized country."""
    settings = get_settings()
    if not settings.iptv_org_epg_dir:
        raise RuntimeError("iptv-org/epg is not configured (DPTV_IPTV_ORG_EPG_DIR is unset)")
    if not entries:
        raise RuntimeError("No channels selected to grab")
    epg_dir = Path(settings.iptv_org_epg_dir)
    timeout = timeout_seconds or settings.iptv_org_grab_timeout_seconds
    batch_size = max(1, settings.iptv_org_grab_batch_size)

    # The subprocess runs with the checkout as its cwd, so a relative output_path would
    # resolve against that directory instead of wherever the caller meant - must be absolute.
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(entries) <= batch_size:
        await _run_grab_batch(entries, output_path, epg_dir, timeout)
        return

    batch_paths: list[Path] = []
    try:
        total_batches = -(-len(entries) // batch_size)  # ceil division
        for batch_num, i in enumerate(range(0, len(entries), batch_size), start=1):
            batch = entries[i : i + batch_size]
            batch_path = output_path.with_name(f"{output_path.stem}.batch{batch_num}{output_path.suffix}")
            logger.info("Grabbing batch %d/%d (%d channels)", batch_num, total_batches, len(batch))
            await _run_grab_batch(batch, batch_path, epg_dir, timeout)
            batch_paths.append(batch_path)
        _merge_xmltv_batches(batch_paths, output_path)
    finally:
        for p in batch_paths:
            p.unlink(missing_ok=True)
