"""Optional "a sync just finished" webhook (Settings -> Integrations -> "After a sync, notify URL").

POSTs {"event": "sync_finished", "kind": "sources"|"epg", "at": <unix seconds>} to the configured
URL after a scheduled or manual source/EPG sync has committed, so a player can refresh right away
instead of waiting for its next poll. Fire-and-forget: it never delays or fails the sync itself,
uses a short timeout, and failures are only logged (host only - the URL may carry a token).
"""

import asyncio
import logging
import time
from urllib.parse import urlsplit

import httpx

from app.config import get_settings

logger = logging.getLogger("dptv.sync_notify")

_TIMEOUT_SECONDS = 5.0
# Strong references so a pending task isn't garbage-collected mid-flight.
_pending: set[asyncio.Task] = set()


def _target() -> str | None:
    url = (get_settings().sync_notify_url or "").strip()
    if not url:
        return None
    if urlsplit(url).scheme not in ("http", "https"):
        logger.warning("Ignoring sync notify URL: only http(s) URLs are supported")
        return None
    return url


async def _post(url: str, payload: dict) -> None:
    host = urlsplit(url).hostname or "?"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("Sent sync_finished(%s) notification to %s", payload["kind"], host)
    except Exception as exc:  # noqa: BLE001 - best effort, never worth failing a sync over
        logger.warning("Sync notify to %s failed: %s", host, type(exc).__name__)


def notify_sync_finished(kind: str) -> None:
    """Call after the sync's changes are committed. `kind` is "sources" or "epg"."""
    url = _target()
    if url is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    payload = {"event": "sync_finished", "kind": kind, "at": int(time.time())}
    task = loop.create_task(_post(url, payload))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
