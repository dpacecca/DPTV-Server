from typing import TYPE_CHECKING

from app.services.iptv_org_epg import get_cached_logo_url

if TYPE_CHECKING:
    from app.models.playlist import PlaylistChannel


def resolve_channel_logo(pc: "PlaylistChannel") -> str | None:
    """Effective logo for a playlist channel: an admin-set override always wins, then whatever
    the provider itself supplied (tvg-logo/stream_icon), then an automatic lookup against
    iptv-org's community-maintained logo database keyed by the mapped EPG channel id - only
    useful when that id follows iptv-org's own "Name.cc" convention (e.g. EPG data sourced
    from iptv-org/epg itself), a no-op fallback otherwise."""
    if pc.logo_url_override:
        return pc.logo_url_override
    if pc.source_channel and pc.source_channel.logo_url:
        return pc.source_channel.logo_url
    if pc.epg_channel:
        return get_cached_logo_url(pc.epg_channel.epg_channel_id)
    return None
