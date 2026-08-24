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
