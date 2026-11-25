"""(1.0.24) What DPTV-Server knows about a provider film for the movie categories it hands players:
TMDB's genres, release date and rating, and TMDB's films trending this week."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MovieInfo(Base):
    __tablename__ = "movie_info"

    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("source_channels.id", ondelete="CASCADE"), primary_key=True
    )
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    release_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    """"YYYY-MM-DD" (or "YYYY" when only the year is known): the provider's, else TMDB's, else the title's year."""
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    popularity: Mapped[float | None] = mapped_column(Float, nullable=True)
    genres: Mapped[str | None] = mapped_column(Text, nullable=True)
    """JSON list of TMDB genre names."""
    tmdb_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    """TMDB was asked (found or not); False while no TMDB key is set, so a later key fills it in."""
    looked_up_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TmdbTrending(Base):
    __tablename__ = "tmdb_trending"

    tmdb_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rank: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
