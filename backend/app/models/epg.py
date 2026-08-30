from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class EpgSource(Base, TimestampMixin):
    __tablename__ = "epg_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source_kind: Mapped[str] = mapped_column(String(20), default="url")
    """"url" (a plain XMLTV/.xml.gz fetch) or "iptv_org" (server-side iptv-org/epg scrape)."""
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Required when source_kind == "url"; unused for "iptv_org" sources."""
    iptv_org_selection: Mapped[str | None] = mapped_column(Text, nullable=True)
    """JSON-encoded {"mode": "country"|"category", "values": [...]} recording what was picked,
    so a refresh can re-resolve the current channel catalog instead of a frozen snapshot, and
    the admin UI can show/edit the selection. Only set when source_kind == "iptv_org"."""
    refresh_interval_minutes: Mapped[int] = mapped_column(Integer, default=720)

    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refresh_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_refresh_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    channels: Mapped[list["EpgChannel"]] = relationship(
        back_populates="epg_source", cascade="all, delete-orphan"
    )


class EpgChannel(Base):
    __tablename__ = "epg_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epg_source_id: Mapped[int] = mapped_column(ForeignKey("epg_sources.id", ondelete="CASCADE"), index=True)
    epg_channel_id: Mapped[str] = mapped_column(String(255), index=True)
    """The <channel id="..."> value from the XMLTV file (tvg-id)."""
    display_name: Mapped[str] = mapped_column(String(500))
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    epg_source: Mapped["EpgSource"] = relationship(back_populates="channels")
    programs: Mapped[list["EpgProgram"]] = relationship(
        back_populates="epg_channel", cascade="all, delete-orphan"
    )


class EpgProgram(Base):
    __tablename__ = "epg_programs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epg_channel_id: Mapped[int] = mapped_column(ForeignKey("epg_channels.id", ondelete="CASCADE"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stop: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(1000))
    subtitle: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)

    epg_channel: Mapped["EpgChannel"] = relationship(back_populates="programs")
