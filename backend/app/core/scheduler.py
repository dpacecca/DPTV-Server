import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.base import SyncTrigger
from app.models.sync import SyncSchedule
from app.services.iptv_org_epg import refresh_logo_cache
from app.services.sync_engine import refresh_iptv_org_channel_catalog, run_full_sync

logger = logging.getLogger("dptv.scheduler")

scheduler = AsyncIOScheduler()


async def _refresh_logo_cache_job() -> None:
    try:
        await refresh_logo_cache()
    except Exception:  # noqa: BLE001 - best-effort background refresh, never worth crashing over
        logger.exception("Failed to refresh iptv-org logo cache")


async def _refresh_iptv_org_channel_catalog_job() -> None:
    async with SessionLocal() as db:
        try:
            count = await refresh_iptv_org_channel_catalog(db)
            await db.commit()
            if count:
                logger.info("Refreshed iptv-org channel catalog: %d channels", count)
        except Exception:  # noqa: BLE001 - best-effort background refresh, never worth crashing over
            logger.exception("Failed to refresh iptv-org channel catalog")
            await db.rollback()


async def _run_scheduled_sync(schedule_id: int) -> None:
    async with SessionLocal() as db:
        schedule = await db.get(SyncSchedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        logger.info("Running scheduled sync (schedule_id=%s)", schedule_id)
        try:
            await run_full_sync(db, SyncTrigger.SCHEDULED)
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
        _refresh_iptv_org_channel_catalog_job,
        trigger=IntervalTrigger(hours=24),
        id="iptv-org-channel-catalog-refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        _refresh_iptv_org_channel_catalog_job,
        trigger=DateTrigger(),
        id="iptv-org-channel-catalog-refresh-initial",
        replace_existing=True,
    )
