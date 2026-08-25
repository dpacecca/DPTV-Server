import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column


def enum_column(enum_cls: type[enum.Enum], length: int = 20):
    """A VARCHAR-backed enum column that round-trips to real Python Enum members.

    Plain `String(20)` columns typed as `Mapped[SomeEnum]` only carry the enum as a static
    type hint - SQLAlchemy still hands back a raw str on read. Using `sa.Enum(..., native_enum=False)`
    makes reads/writes actually coerce through the enum, while still storing plain VARCHAR
    (not a Postgres native ENUM type, which would need a migration every time a value is added).
    """

    return Enum(enum_cls, native_enum=False, length=length, values_callable=lambda e: [x.value for x in e])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChannelType(str, enum.Enum):
    LIVE = "live"
    VOD = "vod"
    SERIES = "series"


class SourceType(str, enum.Enum):
    XTREAM = "xtream"
    M3U = "m3u"


class EpgMatchType(str, enum.Enum):
    NONE = "none"
    AUTO = "auto"
    MANUAL = "manual"


class DummyEpgMode(str, enum.Enum):
    INHERIT = "inherit"
    OFF = "off"
    NAME = "name"
    EVENT = "event"


class SyncTrigger(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    STARTUP = "startup"


class SyncStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class ProbeStatus(str, enum.Enum):
    """Outcome of the last ffprobe quality scan for a channel."""

    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"
    UNREACHABLE = "unreachable"
    NO_VIDEO_STREAM = "no_video_stream"
    NO_URL = "no_url"
