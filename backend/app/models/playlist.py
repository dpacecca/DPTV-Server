from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import ChannelType, DummyEpgMode, EpgMatchType, TimestampMixin, enum_column


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

    category: Mapped["PlaylistCategory"] = relationship(back_populates="channels")
    source_channel: Mapped["SourceChannel | None"] = relationship()
    epg_channel: Mapped["EpgChannel | None"] = relationship()
