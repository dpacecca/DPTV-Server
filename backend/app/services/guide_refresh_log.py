import logging
from collections import deque
from datetime import datetime, timezone

from app.config import get_settings

_buffer: deque[str] = deque(maxlen=get_settings().guide_refresh_log_buffer_size)

# Loggers that carry EPG/guide refresh activity worth surfacing to an admin: scheduled and
# manually-triggered syncs, plus the logo-cache refresh. Attaching to these specific loggers
# rather than the root logger keeps unrelated noise (httpx, apscheduler's own bookkeeping,
# uvicorn access logs) out of this buffer.
_SOURCE_LOGGER_NAMES = ("dptv.iptv_org_epg", "dptv.scheduler", "dptv.epg_refresh_jobs")


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _buffer.append(f"{ts} | {record.name:<20} | {self.format(record)}")


_handler = _BufferHandler()
_handler.setLevel(logging.INFO)
for _name in _SOURCE_LOGGER_NAMES:
    logging.getLogger(_name).addHandler(_handler)


def get_lines(limit: int) -> list[str]:
    if limit >= len(_buffer):
        return list(_buffer)
    return list(_buffer)[-limit:]
