from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DPTV_", extra="ignore")

    database_url: str = "postgresql+asyncpg://dptv:dptv@localhost:5432/dptv"

    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24

    admin_username: str = "admin"
    admin_password: str = "admin"

    public_base_url: str = "http://localhost:8000"
    """Base URL players use to reach this server's XC API (used when generating M3U/links)."""

    data_dir: str = "./data"
    """Where generated M3U/XMLTV output files and DB backups are written."""

    http_timeout_seconds: float = 30.0

    ffprobe_path: str = "ffprobe"
    """Path to the ffprobe binary, used to detect stream resolution/framerate/bitrate when
    scanning a category for duplicate channels. Requires the `ffmpeg` system package."""
    scan_default_concurrency: int = 2
    """How many streams to probe at once by default. Kept low because IPTV providers commonly
    cap concurrent connections per account, and a scan is a background admin action, not
    something that needs to race to finish."""
    scan_max_concurrency: int = 8
    scan_default_timeout_seconds: float = 8.0

    iptv_org_epg_dir: str | None = None
    """Path to a local clone of github.com/iptv-org/epg (with `npm install` already run), used
    to scrape iptv-org's site-specific EPG guides on demand. None disables the feature entirely -
    it's an optional system dependency (Node.js + the vendored checkout), not bundled."""
    iptv_org_grab_timeout_seconds: float = 900.0
    """Some sites (e.g. ones with 500+ channels) genuinely take minutes to scrape."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
