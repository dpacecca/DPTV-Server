import logging
import time
from functools import lru_cache
from pathlib import Path

import httpx

logger = logging.getLogger("dptv.version")

_GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/dpacecca/DPTV-Server/releases/latest"
_LATEST_CACHE_TTL_SECONDS = 3600.0
# (version, monotonic time it was fetched) - checking GitHub on every page load would burn through
# its unauthenticated rate limit fast across even a couple of admins with the app open, and the
# "is this stale" answer doesn't meaningfully change minute to minute anyway.
_latest_cache: tuple[str | None, float] = (None, 0.0)


@lru_cache
def get_current_version() -> str:
    """The version this running instance was deployed from - read once from the VERSION file at
    the repo root (kept in sync with each tagged GitHub release) and cached for the process
    lifetime, since picking up a new value requires a restart anyway."""
    version_path = Path(__file__).resolve().parents[3] / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("Could not read VERSION file at %s", version_path)
        return "unknown"


def _parse_semver(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.lstrip("v").split("."))
    except ValueError:
        return None


async def get_latest_release_version() -> str | None:
    """The latest tagged GitHub release's version - what the admin UI's "Up to date"/"Update
    available" badge compares the running version against. Returns None (never raises) if the
    check can't complete for any reason: no outbound network, GitHub unreachable, no releases
    published yet, rate-limited, etc - this is a best-effort status indicator, not something
    worth failing or blocking a page load over."""
    global _latest_cache
    cached_version, checked_at = _latest_cache
    if time.monotonic() - checked_at < _LATEST_CACHE_TTL_SECONDS:
        return cached_version

    latest: str | None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_GITHUB_RELEASES_LATEST_URL, headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            latest = resp.json()["tag_name"].lstrip("v")
    except Exception:  # noqa: BLE001 - any failure here just means "unknown", not an error to surface
        logger.info("Could not check latest DPTV-Server release", exc_info=True)
        latest = None

    _latest_cache = (latest, time.monotonic())
    return latest


async def get_version_status() -> dict:
    current = get_current_version()
    latest = await get_latest_release_version()

    up_to_date: bool | None = None
    current_parsed = _parse_semver(current)
    latest_parsed = _parse_semver(latest) if latest else None
    if current_parsed is not None and latest_parsed is not None:
        up_to_date = current_parsed >= latest_parsed

    return {"version": current, "latest_version": latest, "up_to_date": up_to_date}
