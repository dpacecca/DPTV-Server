import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models.base import SyncTrigger
from app.models.sync import SyncSchedule
from app.services.iptv_org_epg import refresh_logo_cache
from app.services.sport_refresh import refresh_all_sport_categories
from app.services.sync_engine import run_full_sync

logger = logging.getLogger("dptv.scheduler")

# Explicit UTC rather than APScheduler's default (the server OS's local timezone) - SyncSchedule.
# time_of_day is stored and interpreted as UTC (the admin UI converts to/from the browser's local
# time for display), so this has to be pinned regardless of what timezone the host itself runs in.
scheduler = AsyncIOScheduler(timezone="UTC")


async def _refresh_logo_cache_job() -> None:
    try:
        await refresh_logo_cache()
    except Exception:  # noqa: BLE001 - best-effort background refresh, never worth crashing over
        logger.exception("Failed to refresh iptv-org logo cache")


async def _refresh_sport_categories_job() -> None:
    async with SessionLocal() as db:
        try:
            count = await refresh_all_sport_categories(db)
            logger.info("Refreshed %d live sport categor(y/ies)", count)
        except Exception:  # noqa: BLE001 - best-effort background refresh, never worth crashing over
            logger.exception("Failed to refresh live sport categories")


async def _run_scheduled_sync(schedule_id: int) -> None:
    async with SessionLocal() as db:
        schedule = await db.get(SyncSchedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        parts = [
            name
            for enabled, name in ((schedule.sync_sources, "sources"), (schedule.sync_epg, "epg"))
            if enabled
        ]
        logger.info("Running scheduled sync (schedule_id=%s, syncing %s)", schedule_id, "+".join(parts) or "nothing")
        try:
            await run_full_sync(db, SyncTrigger.SCHEDULED, sync_sources=schedule.sync_sources, sync_epg=schedule.sync_epg)
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled sync failed")
            await db.rollback()


async def reload_schedules(db: AsyncSession) -> None:
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("sync-schedule-"):
            job.remove()

    result = await db.execute(select(SyncSchedule).where(SyncSchedule.enabled.is_(True)))
    for schedule in result.scalars().all():
        trigger = CronTrigger(hour=schedule.time_of_day.hour, minute=schedule.time_of_day.minute)
        scheduler.add_job(
            _run_scheduled_sync,
            trigger=trigger,
            args=[schedule.id],
            id=f"sync-schedule-{schedule.id}",
            replace_existing=True,
        )
    logger.info("Reloaded %d sync schedule(s)", len(scheduler.get_jobs()))


async def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()
    async with SessionLocal() as db:
        await reload_schedules(db)
    scheduler.add_job(
        _refresh_logo_cache_job,
        trigger=IntervalTrigger(hours=24),
        id="iptv-org-logo-cache-refresh",
        replace_existing=True,
    )
    # Fire once immediately in the background so the cache is warm shortly after startup,
    # without delaying the rest of app startup on a network fetch.
    scheduler.add_job(
        _refresh_logo_cache_job,
        trigger=DateTrigger(),
        id="iptv-org-logo-cache-refresh-initial",
        replace_existing=True,
    )
    scheduler.add_job(
        _refresh_sport_categories_job,
        trigger=IntervalTrigger(minutes=get_settings().sport_refresh_interval_minutes),
        id="sport-categories-refresh",
        replace_existing=True,
    )
    # Fire once immediately so a freshly created Live Sport category doesn't sit empty until the
    # first interval elapses.
    scheduler.add_job(
        _refresh_sport_categories_job,
        trigger=DateTrigger(),
        id="sport-categories-refresh-initial",
        replace_existing=True,
    )
