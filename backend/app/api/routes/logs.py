import subprocess

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser
from app.config import get_settings
from app.services import xc_log

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/xc")
async def get_xc_logs(_admin: AdminUser, lines: int = 100) -> dict:
    limit = max(1, min(lines, get_settings().xc_log_buffer_size))
    return {"lines": xc_log.get_lines(limit)}


@router.post("/system/restart")
async def restart_backend(_admin: AdminUser) -> dict:
    """Restarts the whole dptv-backend service - there's no separate "XC server" process to
    bounce independently, this is the one process serving both the XC-protocol API and the
    admin panel itself, so this restarts everything (systemd's Restart=always plus --no-block
    below means the panel just briefly disconnects and reconnects on its own).

    Requires the app's own OS user to have been granted passwordless sudo for exactly this
    command, e.g. via a line in /etc/sudoers.d/dptv-restart:
        dptv ALL=(root) NOPASSWD: /usr/bin/systemctl restart --no-block dptv-backend
    (swap "dptv" for whichever user the systemd unit actually runs as, and the unit name for
    DPTV_SYSTEMD_UNIT_NAME if it's been overridden from the default). Without that grant, this
    fails with a clear permission error rather than silently doing nothing."""
    unit = get_settings().systemd_unit_name
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "--no-block", unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "Restart command timed out") from exc
    except FileNotFoundError as exc:
        raise HTTPException(500, "sudo or systemctl not found on this system") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "sudo permission not configured for this action - see server setup docs"
        raise HTTPException(500, f"Restart failed: {detail}")
    return {"ok": True}
