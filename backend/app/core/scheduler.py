import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.base import SyncTrigger
from app.models.sync import SyncSchedule
from app.services.sync_engine import run_full_sync

logger = logging.getLogger("dptv.scheduler")

scheduler = AsyncIOScheduler()


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
