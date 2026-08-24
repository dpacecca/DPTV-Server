from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import SyncStatus, SyncTrigger, TimestampMixin, enum_column


class SyncSchedule(Base, TimestampMixin):
    """A single time-of-day at which the source+EPG sync should run. Add multiple rows for multiple times/day."""

    __tablename__ = "sync_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    time_of_day: Mapped[time] = mapped_column(Time)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_sources: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_epg: Mapped[bool] = mapped_column(Boolean, default=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[SyncTrigger] = mapped_column(enum_column(SyncTrigger))
    status: Mapped[SyncStatus] = mapped_column(enum_column(SyncStatus), default=SyncStatus.RUNNING)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
