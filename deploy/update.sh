#!/usr/bin/env bash
# Update an existing native (non-Docker) DPTV-Server install to the latest
# code on the current branch: pulls, reinstalls backend deps, rebuilds the
# frontend, then restarts the backend service.
#
# The dptv-backend systemd unit runs `alembic upgrade head` in ExecStartPre
# with the same Environment= vars as the service itself, so restarting the
# service applies any pending DB migrations automatically - no separate
# migration step is needed here.
#
# Usage: run as root (or any user allowed to `git pull`, write into the repo,
# and `systemctl restart dptv-backend`) on the host running DPTV-Server:
#   /opt/DPTV-Server/deploy/update.sh
# Or, once the `update` symlink is installed (see deploy/README.md), just:
#   update

set -euo pipefail

APP_DIR="${DPTV_APP_DIR:-/opt/DPTV-Server}"
SERVICE_NAME="${DPTV_SERVICE_NAME:-dptv-backend}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

[ -d "$APP_DIR" ] || fail "App directory not found: $APP_DIR (set DPTV_APP_DIR to override)"
cd "$APP_DIR"

log "Pulling latest code in $APP_DIR"
git pull || fail "git pull failed - check for local changes/conflicts with 'git status'"

log "Installing backend dependencies"
cd "$APP_DIR/backend"
[ -x .venv/bin/pip ] || fail "backend/.venv not found - is this a valid install?"
.venv/bin/pip install . || fail "backend dependency install failed"

log "Installing frontend dependencies and rebuilding"
cd "$APP_DIR/frontend"
npm install || fail "npm install failed"
npm run build || fail "frontend build failed"

log "Restarting $SERVICE_NAME (this also applies any pending DB migrations)"
systemctl restart "$SERVICE_NAME" || fail "systemctl restart $SERVICE_NAME failed"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    log "Update complete - $SERVICE_NAME is active"
else
    systemctl status "$SERVICE_NAME" --no-pager || true
    fail "$SERVICE_NAME did not come back up - check 'journalctl -u $SERVICE_NAME -n 100' for details"
fi
