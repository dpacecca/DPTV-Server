import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import auth, epg_sources, playlists, scheduler as scheduler_routes, sources, xc_server, xc_users
from app.config import get_settings
from app.core.scheduler import start_scheduler
from app.core.security import hash_password
from app.db import SessionLocal
from app.models.xc_user import AdminUser

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

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(epg_sources.router)
app.include_router(playlists.router)
app.include_router(xc_users.router)
app.include_router(scheduler_routes.router)
app.include_router(xc_server.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
