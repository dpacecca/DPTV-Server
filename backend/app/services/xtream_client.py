from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.models.base import ChannelType
from app.models.source import Source
from app.services.m3u_parser import parse_m3u

settings = get_settings()

_TYPE_ACTIONS: dict[ChannelType, tuple[str, str]] = {
    ChannelType.LIVE: ("get_live_categories", "get_live_streams"),
    ChannelType.VOD: ("get_vod_categories", "get_vod_streams"),
    ChannelType.SERIES: ("get_series_categories", "get_series"),
}


@dataclass
class CategoryData:
    external_id: str
    name: str
    channel_type: ChannelType


@dataclass
class ChannelData:
    category_external_id: str
    external_stream_id: str
    name: str
    stream_type: ChannelType
    tvg_id: str | None = None
    logo_url: str | None = None
    container_extension: str | None = None
    stream_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class XtreamClientError(Exception):
    pass


def _live_url(base_url: str, username: str, password: str, stream_id: str, ext: str = "ts") -> str:
    return f"{base_url.rstrip('/')}/live/{username}/{password}/{stream_id}.{ext}"


def _vod_url(base_url: str, username: str, password: str, stream_id: str, ext: str) -> str:
    return f"{base_url.rstrip('/')}/movie/{username}/{password}/{stream_id}.{ext}"


def _series_episode_url(base_url: str, username: str, password: str, episode_id: str, ext: str) -> str:
    return f"{base_url.rstrip('/')}/series/{username}/{password}/{episode_id}.{ext}"


class XtreamClient:
    def __init__(self, source: Source):
        self.source = source

    async def _get_json(self, client: httpx.AsyncClient, action: str, extra_params: dict | None = None) -> Any:
        params = {"username": self.source.username, "password": self.source.password}
        if action:
            params["action"] = action
        if extra_params:
            params.update(extra_params)
        resp = await client.get(f"{self.source.base_url.rstrip('/')}/player_api.php", params=params)
        resp.raise_for_status()
        return resp.json()

    async def authenticate(self) -> dict:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            data = await self._get_json(client, action="")
            user_info = data.get("user_info", data)
            if str(user_info.get("auth", "1")) == "0":
                raise XtreamClientError("Xtream authentication failed")
            return data

    async def fetch_categories_and_channels(
        self, channel_type: ChannelType
    ) -> tuple[list[CategoryData], list[ChannelData]]:
        cat_action, stream_action = _TYPE_ACTIONS[channel_type]
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            raw_categories = await self._get_json(client, cat_action)
            categories = [
                CategoryData(external_id=str(c["category_id"]), name=c["category_name"], channel_type=channel_type)
                for c in raw_categories
            ]

            raw_streams = await self._get_json(client, stream_action)
            channels: list[ChannelData] = []
            for s in raw_streams:
                stream_id = str(s.get("stream_id") or s.get("series_id"))
                ext = s.get("container_extension") or ("ts" if channel_type == ChannelType.LIVE else "mp4")
                channels.append(
                    ChannelData(
                        category_external_id=str(s.get("category_id")),
                        external_stream_id=stream_id,
                        name=s.get("name", "Unknown"),
                        stream_type=channel_type,
                        tvg_id=s.get("epg_channel_id") or None,
                        logo_url=s.get("stream_icon") or s.get("cover") or None,
                        container_extension=ext,
                        stream_url=self._build_url(channel_type, stream_id, ext),
                        extra={k: v for k, v in s.items() if k not in ("name", "stream_id", "category_id")},
                    )
                )
            return categories, channels

    def _build_url(self, channel_type: ChannelType, stream_id: str, ext: str) -> str:
        base, user, pw = self.source.base_url, self.source.username, self.source.password
        if channel_type == ChannelType.LIVE:
            return _live_url(base, user, pw, stream_id, ext)
        if channel_type == ChannelType.VOD:
            return _vod_url(base, user, pw, stream_id, ext)
        return _series_episode_url(base, user, pw, stream_id, ext)

    async def fetch_series_episodes(self, series_id: str) -> list[ChannelData]:
        """Only used when Source.use_api_for_series is enabled."""
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            data = await self._get_json(client, "get_series_info", {"series_id": series_id})
            channels: list[ChannelData] = []
            for _season, episodes in (data.get("episodes") or {}).items():
                for ep in episodes:
                    ext = ep.get("container_extension", "mp4")
                    episode_id = str(ep.get("id"))
                    channels.append(
                        ChannelData(
                            category_external_id="",
                            external_stream_id=episode_id,
                            name=ep.get("title", f"Episode {episode_id}"),
                            stream_type=ChannelType.SERIES,
                            container_extension=ext,
                            stream_url=self._build_url(ChannelType.SERIES, episode_id, ext),
                            extra=ep,
                        )
                    )
            return channels


async def fetch_m3u_categories_and_channels(source: Source) -> tuple[list[CategoryData], list[ChannelData]]:
    url = source.m3u_url
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text

    entries = parse_m3u(text)
    categories: dict[tuple[str, ChannelType], CategoryData] = {}
    channels: list[ChannelData] = []

    for entry in entries:
        if source.ignore_vod and entry.channel_type == ChannelType.VOD:
            continue
        if source.ignore_series and entry.channel_type == ChannelType.SERIES:
            continue

        group = entry.group_title or "Uncategorized"
        key = (group, entry.channel_type)
        if key not in categories:
            categories[key] = CategoryData(external_id=group, name=group, channel_type=entry.channel_type)

        if source.provider_uses_tokens:
            stream_id = entry.name
        else:
            stream_id = entry.url

        channels.append(
            ChannelData(
                category_external_id=group,
                external_stream_id=stream_id,
                name=entry.name,
                stream_type=entry.channel_type,
                tvg_id=entry.tvg_id,
                logo_url=entry.tvg_logo,
                stream_url=entry.url,
            )
        )

    return list(categories.values()), channels
