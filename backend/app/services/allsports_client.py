import httpx

from app.config import get_settings

HOST = "allsportsapi2.p.rapidapi.com"
"""RapidAPI's "AllSportsApi" product - a wrapper around SofaScore's own data, covering many
sports under one key/host (see allsports_rugby.py and allsports_nfl.py). Shared here since every
sport under it uses the same host, auth headers, and status.type vocabulary."""

# SofaScore's status.type is a small, shared enum across every sport this wraps. "inprogress" is
# the only live value seen; everything dead/abandoned is grouped so a stale-but-still-listed
# match doesn't show as live. Anything else (notably "notstarted") means it hasn't kicked off yet.
_LIVE_STATES = {"inprogress"}
_FINISHED_STATES = {"finished", "postponed", "canceled", "cancelled", "abandoned", "interrupted", "suspended"}


def classify_status(status_type: str) -> tuple[bool, bool]:
    """Returns (is_live, is_finished) - neither true means the match hasn't kicked off yet."""
    s = status_type.strip().lower()
    if s in _LIVE_STATES:
        return True, False
    if s in _FINISHED_STATES:
        return False, True
    return False, False


async def get_json(path: str) -> dict:
    settings = get_settings()
    if not settings.rapidapi_key:
        raise RuntimeError("DPTV_RAPIDAPI_KEY is not configured")

    url = f"https://{HOST}{path}"
    headers = {"x-rapidapi-host": HOST, "x-rapidapi-key": settings.rapidapi_key}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()
