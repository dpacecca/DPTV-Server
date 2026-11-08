from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class EpgSource(Base, TimestampMixin):
    __tablename__ = "epg_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
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
    episode_num: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """Already formatted for display (e.g. "S2 E14") - see epg_parser.format_episode_num()."""
    program_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """Raw XMLTV <date> text (e.g. "2024" or "20240115") - a movie/program's original
    release/air date, not this specific broadcast's. epg_writer pulls just the year out of it
    for the MOVIE_YEAR layout component."""
    is_new: Mapped[bool] = mapped_column(default=False)
    """Whether the source XMLTV marked this showing with a bare <new/> element."""

    epg_channel: Mapped["EpgChannel"] = relationship(back_populates="programs")
