import gzip
from dataclasses import dataclass
from datetime import datetime

from lxml import etree

GZIP_MAGIC = b"\x1f\x8b"


def _maybe_decompress(data: bytes) -> bytes:
    """Transparently unwraps a gzip-compressed XMLTV file (e.g. an EPG source URL pointing at
    guide.xml.gz). Detected by the gzip magic bytes rather than the URL's extension or a
    Content-Encoding header, since a plain static-file server serving a .gz file as its response
    body won't set the latter - the body just *is* gzip data that the client has to unwrap
    itself. Uncompressed XMLTV (starting with "<") passes through untouched."""
    if data[:2] == GZIP_MAGIC:
        return gzip.decompress(data)
    return data


@dataclass
class ParsedEpgChannel:
    epg_channel_id: str
    display_name: str
    icon_url: str | None


@dataclass
class ParsedEpgProgram:
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    subtitle: str | None
    description: str | None
    category: str | None
    episode_num: str | None
    program_date: str | None
    is_new: bool


def format_episode_num(elems) -> str | None:
    """Picks one <episode-num> among however many a <programme> carries (a provider can repeat
    it once per numbering "system") and formats it for display. Prefers system="onscreen" -
    already human-readable as the provider wrote it (e.g. "S2 E14") - over the far more common
    system="xmltv_ns" (zero-based "season.episode.part", e.g. "1.13.0/1" for S2E14), which this
    converts to the same "SxEy" shape onscreen uses. Any other/missing system's text is used
    as-is; empty/whitespace-only text is skipped same as if the element weren't there."""
    by_system: dict[str, str] = {}
    fallback: str | None = None
    for el in elems:
        text = (el.text or "").strip()
        if not text:
            continue
        system = (el.get("system") or "").strip().lower()
        if system:
            by_system[system] = text
        elif fallback is None:
            fallback = text

    if "onscreen" in by_system:
        return by_system["onscreen"]

    ns = by_system.get("xmltv_ns")
    if ns:
        parts = ns.split(".")
        if len(parts) >= 2:
            season_raw, episode_raw = parts[0].strip(), parts[1].split("/")[0].strip()
            if season_raw.isdigit() and episode_raw.isdigit():
                return f"S{int(season_raw) + 1} E{int(episode_raw) + 1}"
        return ns

    if by_system:
        return next(iter(by_system.values()))
    return fallback


def _parse_xmltv_time(value: str) -> datetime:
    # XMLTV time format: "20260824080000 +0000" or without offset.
    value = value.strip()
    fmt = "%Y%m%d%H%M%S %z" if " " in value else "%Y%m%d%H%M%S"
    return datetime.strptime(value, fmt)


def parse_xmltv(xml_bytes: bytes) -> tuple[list[ParsedEpgChannel], list[ParsedEpgProgram]]:
    """Streams the XMLTV file so multi-hundred-MB guides don't blow up memory. Transparently
    handles a gzip-compressed guide (.xml.gz) - see _maybe_decompress."""
    xml_bytes = _maybe_decompress(xml_bytes)
    channels: list[ParsedEpgChannel] = []
    programs: list[ParsedEpgProgram] = []

    context = etree.iterparse(etree_source(xml_bytes), events=("end",), tag=("channel", "programme"))
    for _event, elem in context:
        if elem.tag == "channel":
            cid = elem.get("id")
            if cid:
                name_el = elem.find("display-name")
                icon_el = elem.find("icon")
                channels.append(
                    ParsedEpgChannel(
                        epg_channel_id=cid,
                        display_name=(name_el.text or cid) if name_el is not None else cid,
                        icon_url=icon_el.get("src") if icon_el is not None else None,
                    )
                )
        elif elem.tag == "programme":
            cid = elem.get("channel")
            start_raw, stop_raw = elem.get("start"), elem.get("stop")
            if cid and start_raw and stop_raw:
                title_el = elem.find("title")
                subtitle_el = elem.find("sub-title")
                desc_el = elem.find("desc")
                cat_el = elem.find("category")
                date_el = elem.find("date")
                try:
                    programs.append(
                        ParsedEpgProgram(
                            channel_id=cid,
                            start=_parse_xmltv_time(start_raw),
                            stop=_parse_xmltv_time(stop_raw),
                            title=(title_el.text or "").strip() if title_el is not None else "",
                            subtitle=(subtitle_el.text or None) if subtitle_el is not None else None,
                            description=(desc_el.text or None) if desc_el is not None else None,
                            category=(cat_el.text or None) if cat_el is not None else None,
                            episode_num=format_episode_num(elem.findall("episode-num")),
                            program_date=(date_el.text or None) if date_el is not None else None,
                            is_new=elem.find("new") is not None,
                        )
                    )
                except ValueError:
                    pass
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]

    return channels, programs


def etree_source(xml_bytes: bytes):
    from io import BytesIO

    return BytesIO(xml_bytes)
