"""Test setup: the real models and routes against a throwaway SQLite file (no Postgres needed).

Run from backend/:  pip install -e '.[dev]' && pytest
Set DPTV_TEST_DATABASE_URL (e.g. postgresql+asyncpg://user:pw@host/dptv_test - a database whose
tables may be dropped) to run the same tests against Postgres instead.
"""

import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="dptv-tests-")
os.environ["DPTV_DATABASE_URL"] = os.environ.get("DPTV_TEST_DATABASE_URL") or (
    f"sqlite+aiosqlite:///{Path(_TMP) / 'test.db'}"
)
os.environ.setdefault("DPTV_PUBLIC_BASE_URL", "http://dptv.test:8000")

import pytest  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.sql.functions import now  # noqa: E402


@compiles(now, "sqlite")
def _sqlite_now_ms(_element, _compiler, **_kw):
    # SQLite's CURRENT_TIMESTAMP only has whole-second resolution; Postgres' now() has
    # microseconds. Millisecond resolution keeps updated_at bumps visible within a fast test.
    return "(strftime('%Y-%m-%d %H:%M:%f', 'now'))"


import app.models  # noqa: E402,F401  (registers every model on Base.metadata)
from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
async def _fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture
async def db():
    async with SessionLocal() as session:
        yield session
