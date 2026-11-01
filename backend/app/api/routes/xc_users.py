from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DbSession
from app.models.playlist import Playlist
from app.models.source import Source
from app.models.xc_user import XcUser, XcUserPlaylist

router = APIRouter(prefix="/api/xc-users", tags=["xc-users"])


class XcUserIn(BaseModel):
    username: str
    password: str
    enabled: bool = True
    max_connections: int = 1
    expiry_date: datetime | None = None
    notes: str | None = None
    source_override_id: int | None = None
    """When set, every stream this user requests is resolved against this source's credentials
    instead of whichever source the channel's own SourceChannel belongs to - see
    XcUser.source_override_id's docstring for the reseller-panel use case this is for."""


def _serialize(user: XcUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "password": user.password,
        "enabled": user.enabled,
        "max_connections": user.max_connections,
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "notes": user.notes,
        "source_override_id": user.source_override_id,
        "source_override_name": user.source_override.name if user.source_override else None,
        "playlists": [
            {"playlist_id": link.playlist_id, "playlist_name": link.playlist.name, "enabled": link.enabled}
            for link in user.playlists
        ],
        # The synced player profile (1.0.9): when it was last written, and by which TV.
        "profile": (
            {
                "revision": user.profile.revision,
                "updated_at": user.profile.updated_at.isoformat() if user.profile.updated_at else None,
                "device": user.profile.device,
                "bytes": len(user.profile.data.encode("utf-8")),
            }
            if user.profile
            else None
        ),
    }


def _xc_user_load_options():
    return (
        selectinload(XcUser.playlists).selectinload(XcUserPlaylist.playlist),
        selectinload(XcUser.source_override),
        selectinload(XcUser.profile),
    )


async def _check_source_override(db: DbSession, source_id: int | None) -> None:
    if source_id is not None and await db.get(Source, source_id) is None:
        raise HTTPException(404, "Source not found")


@router.get("")
async def list_xc_users(db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(select(XcUser).options(*_xc_user_load_options()))
    return [_serialize(u) for u in result.unique().scalars().all()]


@router.post("")
async def create_xc_user(payload: XcUserIn, db: DbSession, _admin: AdminUser) -> dict:
    await _check_source_override(db, payload.source_override_id)
    user = XcUser(**payload.model_dump())
    db.add(user)
    await db.commit()
    await db.refresh(user, attribute_names=["playlists", "source_override", "profile"])
    return _serialize(user)


@router.put("/{user_id}")
async def update_xc_user(user_id: int, payload: XcUserIn, db: DbSession, _admin: AdminUser) -> dict:
    await _check_source_override(db, payload.source_override_id)
    user = await db.get(XcUser, user_id, options=[*_xc_user_load_options()])
    if user is None:
        raise HTTPException(404, "XC user not found")
    for key, value in payload.model_dump().items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user, attribute_names=["playlists", "source_override", "profile"])
    return _serialize(user)


@router.delete("/{user_id}")
async def delete_xc_user(user_id: int, db: DbSession, _admin: AdminUser) -> dict:
    user = await db.get(XcUser, user_id)
    if user is None:
        raise HTTPException(404, "XC user not found")
    await db.delete(user)
    await db.commit()
    return {"ok": True}


class PlaylistLinkIn(BaseModel):
    playlist_id: int
    enabled: bool = True


@router.post("/{user_id}/playlists")
async def set_playlist_link(user_id: int, payload: PlaylistLinkIn, db: DbSession, _admin: AdminUser) -> dict:
    user = await db.get(XcUser, user_id)
    if user is None:
        raise HTTPException(404, "XC user not found")
    if await db.get(Playlist, payload.playlist_id) is None:
        raise HTTPException(404, "Playlist not found")
    result = await db.execute(
        select(XcUserPlaylist).where(
            XcUserPlaylist.xc_user_id == user_id, XcUserPlaylist.playlist_id == payload.playlist_id
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        link = XcUserPlaylist(xc_user_id=user_id, playlist_id=payload.playlist_id, enabled=payload.enabled)
        db.add(link)
    else:
        link.enabled = payload.enabled
    await db.commit()
    return {"ok": True}


@router.delete("/{user_id}/playlists/{playlist_id}")
async def remove_playlist_link(user_id: int, playlist_id: int, db: DbSession, _admin: AdminUser) -> dict:
    result = await db.execute(
        select(XcUserPlaylist).where(
            XcUserPlaylist.xc_user_id == user_id, XcUserPlaylist.playlist_id == playlist_id
        )
    )
    link = result.scalar_one_or_none()
    if link:
        await db.delete(link)
        await db.commit()
    return {"ok": True}


@router.delete("/{user_id}/profile")
async def clear_profile(user_id: int, db: DbSession, _admin: AdminUser) -> dict:
    """Forget the player profile synced for this login (1.0.9); the next TV to sync starts it afresh."""
    from app.services import profile_store

    return {"cleared": await profile_store.clear(db, user_id)}
