import re
from dataclasses import dataclass

from app.models.base import ChannelType

ATTR_RE = re.compile(r'([a-zA-Z0-9\-]+)="([^"]*)"')


@dataclass
class M3uEntry:
    name: str
    tvg_id: str | None
    tvg_logo: str | None
    group_title: str | None
    url: str
    channel_type: ChannelType


def _guess_channel_type(url: str) -> ChannelType:
    if "/movie/" in url:
        return ChannelType.VOD
    if "/series/" in url:
        return ChannelType.SERIES
    return ChannelType.LIVE


def parse_m3u(text: str) -> list[M3uEntry]:
    lines = text.splitlines()
    entries: list[M3uEntry] = []
    pending: dict | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            # Name is the text after the last comma on the EXTINF line.
            name = line.rsplit(",", 1)[-1].strip() if "," in line else attrs.get("tvg-name", "Unknown")
            pending = {
                "name": attrs.get("tvg-name") or name,
                "tvg_id": attrs.get("tvg-id") or None,
                "tvg_logo": attrs.get("tvg-logo") or None,
                "group_title": attrs.get("group-title") or None,
            }
        elif line.startswith("#"):
            continue
        else:
            if pending is None:
                continue
            entries.append(
                M3uEntry(
                    name=pending["name"],
                    tvg_id=pending["tvg_id"],
                    tvg_logo=pending["tvg_logo"],
                    group_title=pending["group_title"],
                    url=line,
                    channel_type=_guess_channel_type(line),
                )
            )
            pending = None

    return entries
