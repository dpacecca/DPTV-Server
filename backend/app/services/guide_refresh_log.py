import logging
from collections import deque
from datetime import datetime, timezone

from app.config import get_settings

_buffer: deque[dict] = deque(maxlen=get_settings().guide_refresh_log_buffer_size)

# Loggers that carry EPG/guide refresh activity worth surfacing to an admin: scheduled and
# manually-triggered syncs, plus the logo-cache refresh. Attaching to these specific loggers
# rather than the root logger keeps unrelated noise (httpx, apscheduler's own bookkeeping,
# uvicorn access logs) out of this buffer.
_SOURCE_LOGGER_NAMES = ("dptv.iptv_org_epg", "dptv.scheduler", "dptv.epg_refresh_jobs", "dptv.sync_engine")


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Timestamp shipped as UTC ISO-8601 rather than pre-formatted text - the admin UI
        # localizes it to the viewer's own browser timezone rather than the server's.
        _buffer.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "line": f"{record.name:<20} | {self.format(record)}",
        })


_handler = _BufferHandler()
_handler.setLevel(logging.INFO)
for _name in _SOURCE_LOGGER_NAMES:
    logging.getLogger(_name).addHandler(_handler)


def get_lines(limit: int) -> list[dict]:
    if limit >= len(_buffer):
        return list(_buffer)
    return list(_buffer)[-limit:]
