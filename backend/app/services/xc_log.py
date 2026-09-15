from collections import deque
from datetime import datetime, timezone

from app.config import get_settings

_buffer: deque[str] = deque(maxlen=get_settings().xc_log_buffer_size)

_SENSITIVE_QUERY_KEYS = ("password",)


def _redact_query_string(query: str) -> str:
    """Xtream-Codes clients pass username/password as plain query params on every request -
    that's the protocol, not something this app can change - but there's no reason to keep the
    password readable in a log page any authenticated admin can pull up (and might screenshot).
    Username is left as-is since it's needed to tell which XC user a request belongs to."""
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        if key.lower() in _SENSITIVE_QUERY_KEYS and value:
            parts.append(f"{key}=***")
        else:
            parts.append(pair)
    return "&".join(parts)


def log_request(client_ip: str, method: str, path: str, query: str, status_code: int, duration_ms: float) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    target = f"{path}?{_redact_query_string(query)}" if query else path
    _buffer.append(f"{ts} | {client_ip:<15} | {method:<6} | {status_code} | {duration_ms:6.1f}ms | {target}")


def get_lines(limit: int) -> list[str]:
    if limit >= len(_buffer):
        return list(_buffer)
    return list(_buffer)[-limit:]
