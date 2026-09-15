import asyncio
import subprocess

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser
from app.config import get_settings
from app.services import guide_refresh_log, xc_log

router = APIRouter(prefix="/api/logs", tags=["logs"])

_SYSTEMCTL = "/usr/bin/systemctl"


@router.get("/xc")
async def get_xc_logs(_admin: AdminUser, lines: int = 100) -> dict:
    limit = max(1, min(lines, get_settings().xc_log_buffer_size))
    return {"lines": xc_log.get_lines(limit)}


@router.get("/guide-refresh")
async def get_guide_refresh_logs(_admin: AdminUser, lines: int = 100) -> dict:
    limit = max(1, min(lines, get_settings().guide_refresh_log_buffer_size))
    return {"lines": guide_refresh_log.get_lines(limit)}


async def _fire_restart(unit: str) -> None:
    """Actually issues the restart, a beat after the response for this request has already been
    sent. This process IS the service being restarted, so running the restart synchronously in
    the request handler is a race: systemd starts tearing this process down for the restart
    almost as soon as the command is issued (--no-block only means the CLI returns before the
    restart job finishes, not before it starts), which can kill this process before the HTTP
    response finishes flushing back to the client. Deferring it lets the "restart triggered"
    response land first every time."""
    await asyncio.sleep(0.5)
    subprocess.run(["sudo", "-n", _SYSTEMCTL, "restart", "--no-block", unit], timeout=10)


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
    fails with a clear permission error rather than silently doing nothing.

    The permission check below (`sudo -n -l ...`) only reports whether the command would be
    allowed - it doesn't run anything - so a misconfigured sudoers grant is still reported
    cleanly in the response. The restart itself is deferred; see _fire_restart."""
    unit = get_settings().systemd_unit_name
    try:
        check = subprocess.run(
            ["sudo", "-n", "-l", _SYSTEMCTL, "restart", "--no-block", unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "Restart permission check timed out") from exc
    except FileNotFoundError as exc:
        raise HTTPException(500, "sudo or systemctl not found on this system") from exc
    if check.returncode != 0:
        detail = (check.stderr or check.stdout or "").strip() or "sudo permission not configured for this action - see server setup docs"
        raise HTTPException(500, f"Restart failed: {detail}")
    asyncio.create_task(_fire_restart(unit))
    return {"ok": True}
