import os

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AdminUser
from app.config import get_settings
from app.core.scheduler import reschedule_sport_refresh
from app.services import env_file
from app.services.dummy_epg import list_timezones

router = APIRouter(prefix="/api/settings", tags=["settings"])

ENV_PREFIX = "DPTV_"

# Deliberately left out of the GUI editor, with why - shown to the admin instead of just missing:
EXCLUDED_FIELDS: dict[str, str] = {
    "database_url": (
        "Bound to the database connection this running process already opened - changing it "
        "here wouldn't reconnect anything, and a wrong value would leave the app unable to "
        "start. Edit the .env file directly and restart the server."
    ),
    "admin_username": (
        "Only read once, to create the very first admin account if none exists yet - editing it "
        "here has no effect on an account that already exists."
    ),
    "admin_password": (
        "Same as admin_username above - only seeds the first account. There's currently no "
        "in-app way to change an existing admin's password."
    ),
}

SECRET_FIELDS = {"secret_key", "rapidapi_key"}

# (group, type) per included field - hand-maintained (not introspected from the Settings model)
# since a handful of fields need special handling anyway (secrets masked, display_timezone as a
# select) and the full field list rarely changes.
FIELD_META: dict[str, tuple[str, str]] = {
    "public_base_url": ("Server", "string"),
    "data_dir": ("Server", "string"),
    "http_timeout_seconds": ("Server", "float"),
    "systemd_unit_name": ("Server", "string"),
    "access_token_expire_minutes": ("Security", "int"),
    "secret_key": ("Security", "secret"),
    "xc_log_buffer_size": ("Logging", "int"),
    "guide_refresh_log_buffer_size": ("Logging", "int"),
    "ffprobe_path": ("Duplicate Scanning", "string"),
    "scan_default_concurrency": ("Duplicate Scanning", "int"),
    "scan_max_concurrency": ("Duplicate Scanning", "int"),
    "scan_default_timeout_seconds": ("Duplicate Scanning", "float"),
    "rapidapi_key": ("Live Sport", "secret"),
    "sport_lookahead_days": ("Live Sport", "int"),
    "sport_refresh_interval_minutes": ("Live Sport", "int"),
    "display_timezone": ("Live Sport", "timezone"),
}

FIELD_DESCRIPTIONS: dict[str, str] = {
    "public_base_url": "Base URL players use to reach this server's XC API (used when generating M3U/links).",
    "data_dir": "Where generated M3U/XMLTV output files and DB backups are written.",
    "http_timeout_seconds": "Timeout (seconds) for outbound HTTP requests this server makes (source sync, EPG fetch, etc).",
    "systemd_unit_name": (
        "Name of the systemd unit this process runs under, used by the \"restart backend\" admin "
        "action - only takes effect if this app's OS user has passwordless sudo for exactly "
        "`systemctl restart --no-block <this unit>`."
    ),
    "access_token_expire_minutes": "How long an admin login session stays valid before needing to log in again.",
    "secret_key": (
        "Signs admin login sessions. Changing this immediately invalidates every session "
        "currently logged in, including your own - you'll need to log back in."
    ),
    "xc_log_buffer_size": "How many recent XC-protocol request log lines are kept in memory for the admin log viewer.",
    "guide_refresh_log_buffer_size": "How many recent guide-refresh log lines are kept in memory for the admin log viewer.",
    "ffprobe_path": "Path to the ffprobe binary, used to detect stream resolution/framerate/bitrate when scanning for duplicates.",
    "scan_default_concurrency": "How many streams to probe at once by default during a duplicate scan.",
    "scan_max_concurrency": "Upper limit an admin can raise a scan's concurrency to.",
    "scan_default_timeout_seconds": "Default per-stream timeout (seconds) during a duplicate scan.",
    "rapidapi_key": "RapidAPI key used for Live Sport fixture providers (Rugby, NFL). Required only once a Live Sport category exists.",
    "sport_lookahead_days": "How many days ahead each Live Sport refresh fetches fixtures for. Each day costs one provider API call.",
    "sport_refresh_interval_minutes": "How often Live Sport categories re-fetch fixtures. Lowering this multiplies API call volume - check your provider's monthly quota first.",
    "display_timezone": "IANA zone used wherever server-generated text bakes in a fixed local time (e.g. a Live Sport event's \"Kick off HH:MM\" description).",
}


class SettingField(BaseModel):
    key: str
    group: str
    type: str
    label: str
    description: str
    value: str | int | float | bool | None
    """The secret type's value is a bool (is a value currently set), never the real secret."""
    default: str | int | float | None
    env_override: bool
    """A real process environment variable (not this .env file) is currently setting this field
    - editing it here writes to .env, but won't visibly change anything until that env var is
    removed from wherever the process actually gets its environment from (systemd unit, Docker
    compose, etc)."""


def _label(key: str) -> str:
    return key.replace("_", " ").title()


@router.get("")
async def get_settings_fields(_admin: AdminUser) -> dict:
    settings = get_settings()
    fields = []
    for key, (group, type_) in FIELD_META.items():
        current = getattr(settings, key)
        env_key = f"{ENV_PREFIX}{key.upper()}"
        is_secret = key in SECRET_FIELDS
        fields.append(
            SettingField(
                key=key,
                group=group,
                type=type_,
                label=_label(key),
                description=FIELD_DESCRIPTIONS.get(key, ""),
                value=bool(current) if is_secret else current,
                default=None if is_secret else settings.model_fields[key].default,
                env_override=env_key in os.environ,
            )
        )
    excluded = [{"key": k, "reason": v} for k, v in EXCLUDED_FIELDS.items()]
    return {"fields": [f.model_dump() for f in fields], "excluded": excluded}


@router.get("/timezones")
async def get_timezones(_admin: AdminUser) -> list[str]:
    return list_timezones()


class SettingsUpdate(BaseModel):
    public_base_url: str | None = None
    data_dir: str | None = None
    http_timeout_seconds: float | None = None
    systemd_unit_name: str | None = None
    access_token_expire_minutes: int | None = None
    secret_key: str | None = None
    xc_log_buffer_size: int | None = None
    guide_refresh_log_buffer_size: int | None = None
    ffprobe_path: str | None = None
    scan_default_concurrency: int | None = None
    scan_max_concurrency: int | None = None
    scan_default_timeout_seconds: float | None = None
    rapidapi_key: str | None = None
    sport_lookahead_days: int | None = None
    sport_refresh_interval_minutes: int | None = None
    display_timezone: str | None = None


@router.patch("")
async def update_settings(payload: SettingsUpdate, _admin: AdminUser) -> dict:
    """Only fields actually present in the request body are touched (exclude_unset) - an
    omitted field is left exactly as it was, while one explicitly sent as null clears it back to
    the process default (only meaningful for the optional rapidapi_key)."""
    changes = payload.model_dump(exclude_unset=True)
    env_updates: dict[str, str | None] = {}
    for key, value in changes.items():
        env_key = f"{ENV_PREFIX}{key.upper()}"
        env_updates[env_key] = None if value is None else str(value)

    env_file.write_env_updates(env_updates)
    get_settings.cache_clear()
    new_settings = get_settings()

    if "sport_refresh_interval_minutes" in changes:
        reschedule_sport_refresh(new_settings.sport_refresh_interval_minutes)

    return {"ok": True}
