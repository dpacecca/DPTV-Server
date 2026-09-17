import logging
import subprocess
import time
from functools import lru_cache
from pathlib import Path

import httpx

logger = logging.getLogger("dptv.version")

_GITHUB_MAIN_COMMIT_URL = "https://api.github.com/repos/dpacecca/DPTV-Server/commits/main"
_LATEST_CACHE_TTL_SECONDS = 3600.0
# (commit sha, monotonic time it was fetched) - checking GitHub on every page load would burn
# through its unauthenticated rate limit fast across even a couple of admins with the app open,
# and the "is this stale" answer doesn't meaningfully change minute to minute anyway. checked_at
# is None (not 0.0) until the first real check: time.monotonic()'s epoch is arbitrary (often
# near process/container start, not near zero) and can itself be a small number, so a 0.0
# sentinel could look like "checked very recently" and starve the very first check forever
# within one TTL window - a real, previously-shipped bug, not a hypothetical.
_latest_cache: tuple[str | None, float | None] = (None, None)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@lru_cache
def get_current_version() -> str:
    """A human-friendly release number, purely cosmetic - read once from the VERSION file at the
    repo root and cached for the process lifetime. "Up to date"/"Update available" is judged by
    comparing actual git commits (see get_current_commit_sha/get_latest_main_commit_sha), not
    this number, precisely because a version file only ever changes when someone remembers to
    bump it (and, previously, only ever meant anything once someone also remembered to tag and
    publish a matching GitHub release) - a missed bump here can make the number stale, but can
    never make the status wrong."""
    version_path = _repo_root() / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("Could not read VERSION file at %s", version_path)
        return "unknown"


@lru_cache
def get_current_commit_sha() -> str | None:
    """The actual commit this running instance was deployed from, read directly from the git
    checkout it's running out of - not a file anyone has to remember to update, so it's always
    correct regardless of whether VERSION was bumped. Cached for the process lifetime like
    get_current_version (a new commit means a restart anyway, same as a new VERSION)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not determine the current git commit", exc_info=True)
        return None


async def get_latest_main_commit_sha() -> str | None:
    """The tip commit of main on GitHub right now - what the version badge's "up to
    date"/"update available" status actually compares the running commit against. Deliberately
    not a tagged release: that requires a human to remember to cut one on every merge (in
    practice, one that never actually happened after the first), where main's tip commit is
    always exactly what "the latest code" means and needs no maintenance at all. Returns None
    (never raises) if the check can't complete for any reason - this is a best-effort status
    indicator, not something worth failing or blocking a page load over."""
    global _latest_cache
    cached_sha, checked_at = _latest_cache
    if checked_at is not None and time.monotonic() - checked_at < _LATEST_CACHE_TTL_SECONDS:
        return cached_sha

    latest: str | None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_GITHUB_MAIN_COMMIT_URL, headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            latest = resp.json()["sha"]
    except Exception:  # noqa: BLE001 - any failure here just means "unknown", not an error to surface
        logger.info("Could not check the latest DPTV-Server commit", exc_info=True)
        latest = None

    _latest_cache = (latest, time.monotonic())
    return latest


async def get_version_status() -> dict:
    current_sha = get_current_commit_sha()
    latest_sha = await get_latest_main_commit_sha()

    up_to_date: bool | None = None
    if current_sha is not None and latest_sha is not None:
        up_to_date = current_sha == latest_sha

    return {
        "version": get_current_version(),
        "commit": current_sha[:7] if current_sha else None,
        "latest_commit": latest_sha[:7] if latest_sha else None,
        "up_to_date": up_to_date,
    }
