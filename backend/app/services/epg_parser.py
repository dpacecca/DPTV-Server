from dataclasses import dataclass
from datetime import datetime

from lxml import etree


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


def _parse_xmltv_time(value: str) -> datetime:
    # XMLTV time format: "20260824080000 +0000" or without offset.
    value = value.strip()
    fmt = "%Y%m%d%H%M%S %z" if " " in value else "%Y%m%d%H%M%S"
    return datetime.strptime(value, fmt)


def parse_xmltv(xml_bytes: bytes) -> tuple[list[ParsedEpgChannel], list[ParsedEpgProgram]]:
    """Streams the XMLTV file so multi-hundred-MB guides don't blow up memory."""
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
