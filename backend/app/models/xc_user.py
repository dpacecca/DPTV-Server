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

    source_override_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    """When set, every live/VOD/series stream this user requests is resolved against THIS
    source's credentials instead of whichever source the channel's SourceChannel actually
    belongs to (see xc_server.py's _resolve_and_redirect/_proxy_series_info) - built for sharing
    one playlist across multiple accounts on the same upstream panel (e.g. a reseller panel where
    each account has its own login but an identical channel/stream-id catalog): duplicate the
    Source with the other account's credentials, point this XC user at it, and every channel in
    their assigned playlist(s) plays from that account without duplicating the playlist itself -
    which also means logo/EPG customization on a channel is automatically shared across every
    user, since there's only ever one PlaylistChannel row. Relies on the channel's
    external_stream_id being valid on the override source too; if it isn't, that one channel just
    won't play for this user."""

    playlists: Mapped[list["XcUserPlaylist"]] = relationship(
        back_populates="xc_user", cascade="all, delete-orphan"
    )
    source_override: Mapped["Source | None"] = relationship()


class XcUserPlaylist(Base):
    __tablename__ = "xc_user_playlists"
    __table_args__ = (UniqueConstraint("xc_user_id", "playlist_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    xc_user_id: Mapped[int] = mapped_column(ForeignKey("xc_users.id", ondelete="CASCADE"), index=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    xc_user: Mapped["XcUser"] = relationship(back_populates="playlists")
    playlist: Mapped["Playlist"] = relationship()


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
