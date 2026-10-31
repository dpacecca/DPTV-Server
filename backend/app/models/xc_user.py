from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class XcUser(Base, TimestampMixin):
    __tablename__ = "xc_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_connections: Mapped[int] = mapped_column(Integer, default=1)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    playlists: Mapped[list["XcUserPlaylist"]] = relationship(
        back_populates="xc_user", cascade="all, delete-orphan"
    )
    profile: Mapped["XcUserProfile | None"] = relationship(
        back_populates="xc_user", cascade="all, delete-orphan", uselist=False
    )


class XcUserPlaylist(Base):
    __tablename__ = "xc_user_playlists"
    __table_args__ = (UniqueConstraint("xc_user_id", "playlist_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    xc_user_id: Mapped[int] = mapped_column(ForeignKey("xc_users.id", ondelete="CASCADE"), index=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    xc_user: Mapped["XcUser"] = relationship(back_populates="playlists")
    playlist: Mapped["Playlist"] = relationship()


class XcUserProfile(Base):
    """A player's profile for one XC login (1.0.9): watch history, progress, favourites, the last
    channel, customisations... as DPTV-Player's own backup JSON, opaque to the server. Every TV
    signed in with this login reads and merges it, so they share one profile. [revision] goes up by
    one on each write; a write names the revision it merged from, and a stale one is refused (409)
    so the player merges the newer profile and tries again - nothing a TV wrote is lost."""

    __tablename__ = "xc_user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    xc_user_id: Mapped[int] = mapped_column(ForeignKey("xc_users.id", ondelete="CASCADE"), unique=True, index=True)
    data: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    device: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The name the last TV to write gave itself, for the admin page."""

    xc_user: Mapped["XcUser"] = relationship(back_populates="profile")


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
