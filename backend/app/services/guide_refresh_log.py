import logging
import re
from collections import deque
from datetime import datetime, timezone

from app.config import get_settings

_buffer: deque[str] = deque(maxlen=get_settings().guide_refresh_log_buffer_size)

# Loggers that carry EPG/guide refresh activity worth surfacing to an admin: scheduled and
# manually-triggered syncs, the iptv-org catalog refresh, and (the most useful bit) the
# iptv-org/epg scraper's own per-batch/per-channel progress output, which is otherwise only
# visible via journalctl. Attaching to these specific loggers rather than the root logger keeps
# unrelated noise (httpx, apscheduler's own bookkeeping, uvicorn access logs) out of this buffer.
_SOURCE_LOGGER_NAMES = ("dptv.iptv_org_epg", "dptv.scheduler", "dptv.epg_refresh_jobs")

# Lines relayed verbatim from the (third-party, not ours to change) iptv-org/epg grabber
# subprocess carry a "  grab: " or "  batch N/M: " prefix (see _log_stream in iptv_org_epg.py).
# On a failed per-program lookup that scraper dumps a multi-hundred-line nested object (full
# request/response, raw sockets, TLS internals) to stderr, one buffer line per line of that dump -
# enough to flush every useful progress line out of the ring buffer after just a couple of
# failures. Only relayed lines are filtered down to the "[current/total] ... (N programs)"
# progress format the grabber also prints per channel; the app's own log lines (already terse
# one-liners by construction, e.g. batch/job status) are never touched by this.
_GRAB_RELAY_PREFIX_RE = re.compile(r"^\s*(grab|batch \d+/\d+):\s?(.*)$")
_GRAB_PROGRESS_RE = re.compile(
    r"\[(?P<current>\d+)/(?P<total>\d+)\]\s+\S+\s+\([^)]*\)\s+-\s+(?P<channel>.+?)\s+-\s+.*?\((?P<programs>\d+)\s+programs?\)"
)


def _format_message(message: str) -> str | None:
    """Returns the line to store, or None to drop it entirely (see module docstring above)."""
    relay_match = _GRAB_RELAY_PREFIX_RE.match(message)
    if relay_match is None:
        return message
    relay_label, rest = relay_match.group(1), relay_match.group(2)
    progress_match = _GRAB_PROGRESS_RE.search(rest)
    if progress_match is None:
        return None
    current, total = int(progress_match["current"]), int(progress_match["total"])
    percent = round(100 * current / total) if total else 0
    summary = f"{current}/{total} ({percent}%) - {progress_match['channel']} - {progress_match['programs']} programs"
    return summary if relay_label == "grab" else f"{relay_label}: {summary}"


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        formatted = _format_message(self.format(record))
        if formatted is None:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _buffer.append(f"{ts} | {record.name:<20} | {formatted}")


_handler = _BufferHandler()
_handler.setLevel(logging.INFO)
for _name in _SOURCE_LOGGER_NAMES:
    logging.getLogger(_name).addHandler(_handler)


def get_lines(limit: int) -> list[str]:
    if limit >= len(_buffer):
        return list(_buffer)
    return list(_buffer)[-limit:]
