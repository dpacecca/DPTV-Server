from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DbSession
from app.models.playlist import Playlist
from app.models.xc_user import XcUser, XcUserPlaylist

router = APIRouter(prefix="/api/xc-users", tags=["xc-users"])


class XcUserIn(BaseModel):
    username: str
    password: str
    enabled: bool = True
    max_connections: int = 1
    expiry_date: datetime | None = None
    notes: str | None = None


def _serialize(user: XcUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "password": user.password,
        "enabled": user.enabled,
        "max_connections": user.max_connections,
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "notes": user.notes,
        "playlists": [
            {"playlist_id": link.playlist_id, "playlist_name": link.playlist.name, "enabled": link.enabled}
            for link in user.playlists
        ],
    }


@router.get("")
async def list_xc_users(db: DbSession, _admin: AdminUser) -> list[dict]:
    result = await db.execute(
        select(XcUser).options(selectinload(XcUser.playlists).selectinload(XcUserPlaylist.playlist))
    )
    return [_serialize(u) for u in result.unique().scalars().all()]


@router.post("")
async def create_xc_user(payload: XcUserIn, db: DbSession, _admin: AdminUser) -> dict:
    user = XcUser(**payload.model_dump())
    db.add(user)
    await db.commit()
    await db.refresh(user, attribute_names=["playlists"])
    return _serialize(user)


@router.put("/{user_id}")
async def update_xc_user(user_id: int, payload: XcUserIn, db: DbSession, _admin: AdminUser) -> dict:
    user = await db.get(XcUser, user_id, options=[selectinload(XcUser.playlists).selectinload(XcUserPlaylist.playlist)])
    if user is None:
        raise HTTPException(404, "XC user not found")
    for key, value in payload.model_dump().items():
        setattr(user, key, value)
    await db.commit()
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
