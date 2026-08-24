from app.config import get_settings
from app.models.base import ChannelType
from app.models.playlist import Playlist
from app.models.xc_user import XcUser

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
            tvg_id = pc.epg_channel.epg_channel_id if pc.epg_channel_id and pc.epg_channel else ""
            logo = pc.logo_url_override or (pc.source_channel.logo_url if pc.source_channel else "") or ""
            ext = "ts" if category.channel_type == ChannelType.LIVE else "mp4"
            url = playlist_channel_stream_url(xc_user, category.channel_type, pc.id, ext)
            attrs = f'tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{category.name}"'
            number = f' tvg-chno="{pc.number}"' if pc.number else ""
            lines.append(f'#EXTINF:-1 {attrs}{number},{pc.name}')
            lines.append(url)
    return "\n".join(lines) + "\n"
