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


@lru_cache
def get_settings() -> Settings:
    return Settings()
