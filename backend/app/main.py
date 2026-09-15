import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import auth, epg_sources, logs, playlists, scheduler as scheduler_routes, sources, xc_server, xc_users
from app.config import get_settings
from app.core.scheduler import start_scheduler
from app.core.security import hash_password
from app.db import SessionLocal
from app.models.xc_user import AdminUser
from app.services import xc_log

logging.basicConfig(level=logging.INFO)
settings = get_settings()


async def _ensure_admin_user() -> None:
    async with SessionLocal() as db:
        result = await db.execute(select(AdminUser).where(AdminUser.username == settings.admin_username))
        if result.scalar_one_or_none() is None:
            db.add(AdminUser(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
            await db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _ensure_admin_user()
    await start_scheduler()
    yield


app = FastAPI(title="DPTV-Server", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_NON_XC_ROOT_PATHS = {"/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def _xc_access_log_middleware(request: Request, call_next):
    """Every XC-protocol route (player_api.php, get.php, xmltv.php, /live|movie|series/...) is
    mounted at the root path rather than under /api, precisely so it matches what Xtream-Codes
    player apps expect - which conveniently also makes "not under /api" a reliable way to
    identify XC client traffic for the admin log viewer, without needing to touch every route in
    xc_server.py individually."""
    is_xc_request = not request.url.path.startswith("/api") and request.url.path not in _NON_XC_ROOT_PATHS
    if not is_xc_request:
        return await call_next(request)
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    client_ip = request.client.host if request.client else "-"
    xc_log.log_request(client_ip, request.method, request.url.path, request.url.query, response.status_code, duration_ms)
    return response


app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(epg_sources.router)
app.include_router(playlists.router)
app.include_router(xc_users.router)
app.include_router(scheduler_routes.router)
app.include_router(xc_server.router)
app.include_router(logs.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
