from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import ChannelType, DummyEpgMode, EpgMatchType, ProbeStatus, TimestampMixin, enum_column


class Playlist(Base, TimestampMixin):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    xc_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    m3u_output_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    m3u_filename: Mapped[str] = mapped_column(String(255), default="playlist.m3u")
    epg_output_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    epg_filename: Mapped[str] = mapped_column(String(255), default="epg.xml")
    epg_days_to_keep: Mapped[int | None] = mapped_column(Integer, nullable=True)

    categories: Mapped[list["PlaylistCategory"]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan", order_by="PlaylistCategory.sort_order"
    )


class DummyEpgRule(Base, TimestampMixin):
    """A custom regex tried against a channel's name when its dummy EPG mode is "event", before
    falling back to the built-in date/time parser. Playlist-wide (not per-category) - which
    channels use "event" mode at all is already controlled by dummy_epg_mode, so this is just
    "how" event mode parses, tried in sort_order with the first match winning."""

    __tablename__ = "dummy_epg_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    pattern: Mapped[str] = mapped_column(Text)
    """Python regex. Must define named groups (?P<hour>..) and (?P<minute>..); optionally
    (?P<ampm>..), (?P<month>..), (?P<day>..), (?P<year>..), and (?P<title>..) (the cleaned
    program title - if omitted, the matched portion is stripped out of the name instead)."""
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class PlaylistCategory(Base, TimestampMixin):
    __tablename__ = "playlist_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    channel_type: Mapped[ChannelType] = mapped_column(enum_column(ChannelType))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    dummy_epg_for_unassigned: Mapped[bool] = mapped_column(Boolean, default=False)
    dummy_epg_program_minutes: Mapped[int] = mapped_column(Integer, default=60)

    playlist: Mapped["Playlist"] = relationship(back_populates="categories")
    channels: Mapped[list["PlaylistChannel"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", order_by="PlaylistChannel.sort_order"
    )
    source_links: Mapped[list["PlaylistCategorySourceLink"]] = relationship(
        back_populates="playlist_category", cascade="all, delete-orphan"
    )


class PlaylistCategorySourceLink(Base):
    """New Channel Manager: source categories whose newly-added channels auto-import here."""

    __tablename__ = "playlist_category_source_links"
    __table_args__ = (UniqueConstraint("playlist_category_id", "source_category_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_category_id: Mapped[int] = mapped_column(
        ForeignKey("playlist_categories.id", ondelete="CASCADE"), index=True
    )
    source_category_id: Mapped[int] = mapped_column(
        ForeignKey("source_categories.id", ondelete="CASCADE"), index=True
    )

    playlist_category: Mapped["PlaylistCategory"] = relationship(back_populates="source_links")


class PlaylistChannel(Base, TimestampMixin):
    __tablename__ = "playlist_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_category_id: Mapped[int] = mapped_column(
        ForeignKey("playlist_categories.id", ondelete="CASCADE"), index=True
    )
    source_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_channels.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(500))
    manual_stream_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    """Playable URL for channels not tied to a Source (added manually)."""
    name_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    """'Ignore Name Changes' - when True, sync will not overwrite `name` from the source."""
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_url_override: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    epg_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("epg_channels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    epg_match_type: Mapped[EpgMatchType] = mapped_column(enum_column(EpgMatchType), default=EpgMatchType.NONE)

    dummy_epg_mode: Mapped[DummyEpgMode] = mapped_column(enum_column(DummyEpgMode), default=DummyEpgMode.INHERIT)
    dummy_epg_program_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    detected_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_bitrate_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_status: Mapped[ProbeStatus | None] = mapped_column(enum_column(ProbeStatus), nullable=True)
    last_probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """Quality metrics from the last "scan for duplicates" ffprobe pass, if this channel has
    ever been scanned. Used both to rank duplicate candidates and to tag resolution into names."""

    category: Mapped["PlaylistCategory"] = relationship(back_populates="channels")
    source_channel: Mapped["SourceChannel | None"] = relationship()
    epg_channel: Mapped["EpgChannel | None"] = relationship()
