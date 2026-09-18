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

    systemd_unit_name: str = "dptv-backend"
    """Name of the systemd unit this process runs under - used by the "restart backend" admin
    action (see app/api/routes/logs.py). Only takes effect if the app's own OS user has been
    granted passwordless sudo for exactly `systemctl restart --no-block <this unit>`; otherwise
    that action fails with a clear permission error rather than doing anything."""

    xc_log_buffer_size: int = 2000
    """How many recent XC-protocol request log lines (see app/services/xc_log.py) are kept in
    memory for the admin log viewer. Bounded and in-memory rather than a file - this is meant
    for "what's hitting the XC API right now", not a durable audit log, and resets on restart."""

    guide_refresh_log_buffer_size: int = 2000
    """How many recent guide-refresh log lines (see app/services/guide_refresh_log.py) are kept
    in memory for the admin log viewer - scheduled/manual EPG sync activity. Bounded and
    in-memory rather than a file, resets on restart."""

    ffprobe_path: str = "ffprobe"
    """Path to the ffprobe binary, used to detect stream resolution/framerate/bitrate when
    scanning a category for duplicate channels. Requires the `ffmpeg` system package."""
    scan_default_concurrency: int = 2
    """How many streams to probe at once by default. Kept low because IPTV providers commonly
    cap concurrent connections per account, and a scan is a background admin action, not
    something that needs to race to finish."""
    scan_max_concurrency: int = 8
    scan_default_timeout_seconds: float = 8.0

    rapidapi_key: str | None = None
    """RapidAPI key used for live-sport fixture providers (e.g. Rugby Live Data) behind a Live
    Sport category (see app/services/sport_data.py). Required only once a Live Sport category
    exists - without it, that category's refresh fails with a clear error recorded on the
    category, the same way an EPG source records a fetch failure."""
    sport_refresh_interval_minutes: int = 30
    """How often the scheduler re-fetches today's fixtures and recomputes which channels are
    showing a live match, for every Live Sport category across every playlist. Lower values give
    more up-to-date "is this live right now" status at the cost of more requests against
    whatever rate limit the configured provider key has."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
