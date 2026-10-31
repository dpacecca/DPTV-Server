import json
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import RedirectResponse

from app.api.deps import DbSession
from app.config import get_settings
from app.models.base import ChannelType
from app.models.epg import EpgProgram
from app.models.playlist import Playlist, PlaylistCategory, PlaylistChannel
from app.models.source import Source
from app.models.xc_user import XcUser, XcUserPlaylist
from app.services.channel_logo import resolve_channel_logo
from app.services.epg_writer import build_xmltv, xmltv_channel_id
from app.services import profile_store
from app.services.m3u_writer import build_m3u
from app.services.sync_status import Rev, compute_sync_status, last_modified_for, unix
from app.services.version import get_current_version
from app.services.xtream_client import XtreamClient

router = APIRouter(tags=["xc-server"])
settings = get_settings()


async def _authenticate(db: AsyncSession, username: str | None, password: str | None) -> XcUser:
    if not username or not password:
        raise HTTPException(401, "Missing credentials")
    result = await db.execute(select(XcUser).where(XcUser.username == username, XcUser.password == password))
    user = result.scalar_one_or_none()
    if user is None or not user.enabled:
        raise HTTPException(401, "Invalid credentials")
    if user.expiry_date and user.expiry_date < datetime.now(timezone.utc):
        raise HTTPException(401, "Account expired")
    return user


async def _enabled_playlists(db: AsyncSession, user: XcUser) -> list[Playlist]:
    result = await db.execute(
        select(Playlist)
        .join(XcUserPlaylist, XcUserPlaylist.playlist_id == Playlist.id)
        .where(
            XcUserPlaylist.xc_user_id == user.id,
            XcUserPlaylist.enabled.is_(True),
            Playlist.enabled.is_(True),
            Playlist.xc_enabled.is_(True),
        )
        .options(
            selectinload(Playlist.categories)
            .selectinload(PlaylistCategory.channels)
            .selectinload(PlaylistChannel.source_channel),
            selectinload(Playlist.categories).selectinload(PlaylistCategory.channels).selectinload(PlaylistChannel.epg_channel),
        )
    )
    return list(result.unique().scalars().all())


def _user_info(user: XcUser) -> dict:
    return {
        "username": user.username,
        "password": user.password,
        "auth": 1,
        "status": "Active" if user.enabled else "Disabled",
        "exp_date": str(int(user.expiry_date.timestamp())) if user.expiry_date else None,
        "is_trial": "0",
        "active_cons": "0",
        "max_connections": str(user.max_connections),
        "allowed_output_formats": ["ts", "m3u8"],
    }


def _server_info() -> dict:
    from urllib.parse import urlparse

    parsed = urlparse(settings.public_base_url)
    return {
        "url": parsed.hostname or "localhost",
        "port": str(parsed.port or 80),
        "https_port": str(parsed.port or 443),
        "server_protocol": parsed.scheme or "http",
        "timezone": "UTC",
        "timestamp_now": int(datetime.now(timezone.utc).timestamp()),
        "time_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _live_stream_json(cat: PlaylistCategory, pc: PlaylistChannel) -> dict:
    return {
        "num": pc.number or pc.id,
        "name": pc.name,
        "stream_type": "live",
        "stream_id": pc.id,
        "stream_icon": resolve_channel_logo(pc) or "",
        "epg_channel_id": xmltv_channel_id(pc),
        "category_id": str(cat.id),
        "custom_sid": "",
        "tv_archive": 0,
        "direct_source": "",
    }


def _vod_stream_json(cat: PlaylistCategory, pc: PlaylistChannel) -> dict:
    ext = pc.source_channel.container_extension if pc.source_channel else "mp4"
    return {
        "num": pc.id,
        "name": pc.name,
        "stream_type": "movie",
        "stream_id": pc.id,
        "stream_icon": resolve_channel_logo(pc) or "",
        "category_id": str(cat.id),
        "container_extension": ext,
    }


def _series_json(cat: PlaylistCategory, pc: PlaylistChannel) -> dict:
    return {
        "num": pc.id,
        "series_id": pc.id,
        "name": pc.name,
        "cover": resolve_channel_logo(pc) or "",
        "category_id": str(cat.id),
    }


# --- Conditional-request support -----------------------------------------------------------------
#
# get.php, xmltv.php and the player_api.php list actions carry an ETag built from the user's
# sync-status revision(s) (services/sync_status.py) plus Last-Modified and Cache-Control: no-cache,
# and answer a matching If-None-Match / If-Modified-Since with 304 before building the body.
# Clients that send neither header get exactly the same body as before.

_LIST_ACTION_KINDS = {
    "get_live_categories": "live",
    "get_live_streams": "live",
    "get_vod_categories": "vod",
    "get_vod_streams": "vod",
    "get_series_categories": "series",
    "get_series": "series",
}


def _validator_headers(user: XcUser, key: str, rev: Rev) -> dict[str, str]:
    last_modified = last_modified_for(user.id, key, rev)
    return {
        "ETag": f'"{rev.rev}"',
        "Last-Modified": format_datetime(last_modified, usegmt=True),
        "Cache-Control": "no-cache",
    }


def _is_not_modified(request: Request, headers: dict[str, str]) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        # RFC 9110 13.1.2/13.2.2: If-None-Match wins over If-Modified-Since; weak comparison
        # (a proxy like nginx with gzip on may hand our ETag back as W/"...").
        etag = headers["ETag"]
        tags = [t.strip() for t in if_none_match.split(",")]
        return any(t == "*" or t.removeprefix("W/") == etag for t in tags)
    if_modified_since = request.headers.get("if-modified-since")
    if if_modified_since:
        try:
            since = parsedate_to_datetime(if_modified_since)
        except (TypeError, ValueError):
            return False
        if since is None:
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return parsedate_to_datetime(headers["Last-Modified"]) <= since
    return False


def _not_modified_response(headers: dict[str, str]) -> Response:
    return Response(status_code=304, headers=headers)


def _head_response(headers: dict[str, str], media_type: str) -> Response:
    response = Response(status_code=200, headers=headers, media_type=media_type)
    # The real length isn't known without building the body, which is exactly what HEAD avoids.
    del response.headers["content-length"]
    return response


def _sync_status_json(status) -> dict:
    out: dict = {"server": "DPTV-Server", "version": get_current_version()}
    for kind in ("live", "vod", "series", "epg"):
        rev = status.revs[kind]
        out[kind] = {"rev": rev.rev, "updated_at": unix(rev.updated_at)}
    out["last_source_sync_at"] = unix(status.last_source_sync_at)
    out["last_epg_refresh_at"] = unix(status.last_epg_refresh_at)
    return out


@router.get("/player_api.php")
async def player_api(
    request: Request,
    response: Response,
    db: DbSession,
    username: str | None = None,
    password: str | None = None,
    action: str | None = None,
    category_id: str | None = None,
    stream_id: int | None = None,
    series_id: int | None = None,
):
    user = await _authenticate(db, username, password)

    if not action:
        # No validators here: server_info.timestamp_now changes every second, so a 304 would
        # hand a client a stale clock.
        return {"user_info": _user_info(user), "server_info": _server_info()}

    if action == "dptv_sync_status":
        response.headers["Cache-Control"] = "no-cache"
        out = _sync_status_json(await compute_sync_status(db, user))
        # The shared player profile (1.0.9): a TV that sees a new revision pulls and merges it.
        stored = await profile_store.read(db, user)
        out["profile"] = {"rev": str(stored.revision), "updated_at": unix(stored.updated_at)}
        return out

    if action == "dptv_profile":
        stored = await profile_store.read(db, user)
        # The data is the player's own JSON, stored as sent: spliced in, not parsed and dumped again.
        body = (
            '{"revision":' + str(stored.revision)
            + ',"updated_at":' + json.dumps(unix(stored.updated_at))
            + ',"device":' + json.dumps(stored.device)
            + ',"data":' + (stored.data or "null") + "}"
        )
        return Response(content=body, media_type="application/json", headers={"Cache-Control": "no-store"})

    if action == "get_series_info":
        return await _proxy_series_info(db, series_id)

    if action in ("get_short_epg", "get_simple_data_table"):
        return await _short_epg(db, stream_id)

    kind = _LIST_ACTION_KINDS.get(action)
    if kind is None:
        raise HTTPException(400, f"Unsupported action: {action}")

    status = await compute_sync_status(db, user)
    validators = _validator_headers(user, kind, status.revs[kind])
    if _is_not_modified(request, validators):
        return _not_modified_response(validators)
    response.headers.update(validators)

    playlists = await _enabled_playlists(db, user)
    all_categories: list[PlaylistCategory] = [c for pl in playlists for c in pl.categories]

    if action == "get_live_categories":
        return [
            {"category_id": str(c.id), "category_name": c.name, "parent_id": 0}
            for c in all_categories
            if c.channel_type == ChannelType.LIVE
        ]
    if action == "get_vod_categories":
        return [
            {"category_id": str(c.id), "category_name": c.name, "parent_id": 0}
            for c in all_categories
            if c.channel_type == ChannelType.VOD
        ]
    if action == "get_series_categories":
        return [
            {"category_id": str(c.id), "category_name": c.name, "parent_id": 0}
            for c in all_categories
            if c.channel_type == ChannelType.SERIES
        ]

    if action == "get_live_streams":
        out = []
        for c in all_categories:
            if c.channel_type != ChannelType.LIVE:
                continue
            if category_id and str(c.id) != category_id:
                continue
            out.extend(_live_stream_json(c, pc) for pc in c.channels if pc.enabled)
        return out

    if action == "get_vod_streams":
        out = []
        for c in all_categories:
            if c.channel_type != ChannelType.VOD:
                continue
            if category_id and str(c.id) != category_id:
                continue
            out.extend(_vod_stream_json(c, pc) for pc in c.channels if pc.enabled)
        return out

    if action == "get_series":
        out = []
        for c in all_categories:
            if c.channel_type != ChannelType.SERIES:
                continue
            if category_id and str(c.id) != category_id:
                continue
            out.extend(_series_json(c, pc) for pc in c.channels if pc.enabled)
        return out

    raise HTTPException(400, f"Unsupported action: {action}")  # unreachable: kind was matched above


class ProfilePut(BaseModel):
    base_revision: int
    """The revision this profile was merged from (0 when the server had none)."""
    data: dict
    device: str | None = None


@router.post("/player_api.php")
async def player_api_post(
    payload: ProfilePut,
    db: DbSession,
    username: str | None = None,
    password: str | None = None,
    action: str | None = None,
):
    """Writes for DPTV-Player (1.0.9); every other action stays GET-only. A write based on an old
    revision is refused with 409 and the current revision, so the player merges that and retries."""
    user = await _authenticate(db, username, password)
    if action != "dptv_profile_put":
        raise HTTPException(400, f"Unsupported action: {action}")
    try:
        stored = await profile_store.write(db, user, payload.base_revision, payload.data, payload.device)
    except profile_store.StaleRevision as stale:
        raise HTTPException(409, {"error": "stale", "revision": stale.current})
    except ValueError:
        raise HTTPException(413, "Profile too large")
    return {"revision": stored.revision, "updated_at": unix(stored.updated_at)}


async def _short_epg(db: AsyncSession, stream_id: int | None, limit: int = 4) -> dict:
    if stream_id is None:
        return {"epg_listings": []}
    pc = await db.get(PlaylistChannel, stream_id, options=[selectinload(PlaylistChannel.epg_channel)])
    if pc is None or not pc.epg_channel_id:
        return {"epg_listings": []}
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(EpgProgram)
        .where(EpgProgram.epg_channel_id == pc.epg_channel_id, EpgProgram.stop > now)
        .order_by(EpgProgram.start)
        .limit(limit)
    )
    listings = []
    for prog in result.scalars().all():
        listings.append(
            {
                "id": str(prog.id),
                "epg_id": str(pc.epg_channel_id),
                "title": prog.title,
                "description": prog.description or "",
                "start": prog.start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": prog.stop.strftime("%Y-%m-%d %H:%M:%S"),
                "now_playing": 1 if prog.start <= now <= prog.stop else 0,
            }
        )
    return {"epg_listings": listings}


async def _proxy_series_info(db: AsyncSession, series_id: int | None) -> dict:
    if series_id is None:
        raise HTTPException(400, "series_id is required")
    pc = await db.get(PlaylistChannel, series_id, options=[selectinload(PlaylistChannel.source_channel)])
    if pc is None or pc.source_channel is None:
        return {"episodes": {}}
    sc = pc.source_channel
    # Walk source_channel -> source_category -> source to get provider credentials.
    from app.models.source import SourceCategory

    category = await db.get(SourceCategory, sc.source_category_id)
    source = await db.get(Source, category.source_id) if category else None
    if source is None:
        return {"episodes": {}}

    client = XtreamClient(source)
    episodes = await client.fetch_series_episodes(sc.external_stream_id)
    episodes_by_season: dict[str, list[dict]] = {"1": []}
    for ep in episodes:
        episodes_by_season["1"].append(
            {
                "id": f"{source.id}_{ep.external_stream_id}",
                "title": ep.name,
                "container_extension": ep.container_extension,
                "direct_source": (
                    f"{settings.public_base_url.rstrip('/')}/series/passthrough/"
                    f"{source.id}_{ep.external_stream_id}.{ep.container_extension}"
                ),
            }
        )
    return {"episodes": episodes_by_season}


@router.api_route("/get.php", methods=["GET", "HEAD"])
async def get_m3u(request: Request, db: DbSession, username: str | None = None, password: str | None = None):
    user = await _authenticate(db, username, password)
    status = await compute_sync_status(db, user)
    validators = _validator_headers(user, "m3u", status.combined(("live", "vod", "series")))
    if _is_not_modified(request, validators):
        return _not_modified_response(validators)
    if request.method == "HEAD":
        return _head_response(validators, "application/x-mpegurl")
    playlists = await _enabled_playlists(db, user)
    lines = ["#EXTM3U"]
    for pl in playlists:
        body = await build_m3u(db, pl, user)
        lines.extend(body.splitlines()[1:])
    return Response(content="\n".join(lines) + "\n", media_type="application/x-mpegurl", headers=validators)


@router.api_route("/xmltv.php", methods=["GET", "HEAD"])
async def get_xmltv(request: Request, db: DbSession, username: str | None = None, password: str | None = None):
    user = await _authenticate(db, username, password)
    status = await compute_sync_status(db, user)
    validators = _validator_headers(user, "epg", status.revs["epg"])
    if _is_not_modified(request, validators):
        return _not_modified_response(validators)
    if request.method == "HEAD":
        return _head_response(validators, "application/xml")
    playlists = await _enabled_playlists(db, user)
    parts = []
    for pl in playlists:
        xml = await build_xmltv(db, pl)
        text = xml.decode("utf-8")
        inner = text.split("<tv", 1)[1].split(">", 1)[1].rsplit("</tv>", 1)[0]
        parts.append(inner)
    combined = f'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="DPTV-Server">{"".join(parts)}</tv>\n'
    return Response(content=combined, media_type="application/xml", headers=validators)


async def _resolve_and_redirect(db: AsyncSession, username: str, password: str, channel_id: int) -> RedirectResponse:
    await _authenticate(db, username, password)
    pc = await db.get(PlaylistChannel, channel_id, options=[selectinload(PlaylistChannel.source_channel)])
    if pc is None or not pc.enabled:
        raise HTTPException(404, "Stream not found")
    target = pc.manual_stream_url or (pc.source_channel.stream_url if pc.source_channel else None)
    if not target:
        raise HTTPException(404, "Stream not found")
    return RedirectResponse(target, status_code=302)


@router.get("/live/{username}/{password}/{channel_id}.{ext}")
async def live_stream(username: str, password: str, channel_id: int, ext: str, db: DbSession):
    return await _resolve_and_redirect(db, username, password, channel_id)


@router.get("/movie/{username}/{password}/{channel_id}.{ext}")
async def vod_stream(username: str, password: str, channel_id: int, ext: str, db: DbSession):
    return await _resolve_and_redirect(db, username, password, channel_id)


@router.get("/series/passthrough/{composite_id}.{ext}")
async def series_episode_stream(composite_id: str, ext: str, db: DbSession):
    try:
        source_id_str, episode_id = composite_id.split("_", 1)
        source_id = int(source_id_str)
    except ValueError as exc:
        raise HTTPException(400, "Invalid episode reference") from exc
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    from app.services.xtream_client import _series_episode_url

    url = _series_episode_url(source.base_url, source.username, source.password, episode_id, ext)
    return RedirectResponse(url, status_code=302)
