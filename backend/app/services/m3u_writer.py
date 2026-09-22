from app.config import get_settings
from app.models.base import ChannelType
from app.models.playlist import Playlist
from app.models.xc_user import XcUser
from app.services.channel_logo import resolve_channel_logo
from app.services.epg_writer import xmltv_channel_id

settings = get_settings()


def playlist_channel_stream_url(
    xc_user: XcUser, channel_type: ChannelType, playlist_channel_id: int, ext: str = "ts"
) -> str:
    kind = {"live": "live", "vod": "movie", "series": "series"}[channel_type.value]
    return (
        f"{settings.public_base_url.rstrip('/')}/{kind}/"
        f"{xc_user.username}/{xc_user.password}/{playlist_channel_id}.{ext}"
    )


def build_m3u(playlist: Playlist, xc_user: XcUser) -> str:
    lines = ["#EXTM3U"]
    for category in playlist.categories:
        for pc in category.channels:
            if not pc.enabled:
                continue
            # Must match the `<channel id>` build_xmltv() files this channel's guide data under -
            # never the provider's own raw EPG channel id string, which never appears in the
            # XMLTV output at all (see xmltv_channel_id's docstring for why that broke every
            # player's guide, not just newly-added channels).
            tvg_id = xmltv_channel_id(pc)
            logo = resolve_channel_logo(pc) or ""
            ext = "ts" if category.channel_type == ChannelType.LIVE else "mp4"
            url = playlist_channel_stream_url(xc_user, category.channel_type, pc.id, ext)
            attrs = f'tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{category.name}"'
            number = f' tvg-chno="{pc.number}"' if pc.number else ""
            lines.append(f'#EXTINF:-1 {attrs}{number},{pc.name}')
            lines.append(url)
    return "\n".join(lines) + "\n"
