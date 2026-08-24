from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import ChannelType, SourceType, TimestampMixin, enum_column


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    type: Mapped[SourceType] = mapped_column(enum_column(SourceType))

    # Xtream API fields
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # M3U fields
    m3u_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    m3u_uses_userpass: Mapped[bool] = mapped_column(Boolean, default=False)

    prefix: Mapped[str] = mapped_column(String(50), default="")
    suffix: Mapped[str] = mapped_column(String(50), default="")
    color: Mapped[str] = mapped_column(String(20), default="#4dabf7")

    ignore_vod: Mapped[bool] = mapped_column(Boolean, default=False)
    ignore_series: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_sync_on_start: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_enable_new_groups: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_clear_removed_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_uses_tokens: Mapped[bool] = mapped_column(Boolean, default=False)
    use_api_for_series: Mapped[bool] = mapped_column(Boolean, default=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    categories: Mapped[list["SourceCategory"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class SourceCategory(Base, TimestampMixin):
    __tablename__ = "source_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    channel_type: Mapped[ChannelType] = mapped_column(enum_column(ChannelType))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    """Position in the provider's own category list, refreshed on every sync - lets an import
    preserve the provider's category order instead of whatever order they happened to sync in."""
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped["Source"] = relationship(back_populates="categories")
    channels: Mapped[list["SourceChannel"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )


class SourceChannel(Base, TimestampMixin):
    __tablename__ = "source_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_category_id: Mapped[int] = mapped_column(
        ForeignKey("source_categories.id", ondelete="CASCADE"), index=True
    )
    external_stream_id: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(500))
    stream_type: Mapped[ChannelType] = mapped_column(enum_column(ChannelType))
    tvg_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    container_extension: Mapped[str | None] = mapped_column(String(20), nullable=True)
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Fully-formed playable URL as returned by provider, used for pass-through redirects."""

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    category: Mapped["SourceCategory"] = relationship(back_populates="channels")
