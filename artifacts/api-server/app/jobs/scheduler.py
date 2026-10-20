import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.mailer import ReminderSettings
from app.services.reminders import REMINDER_TIMEZONE, run_expiry_check

logger = logging.getLogger(__name__)


def scheduled_check():
    try:
        result = run_expiry_check()
        logger.log(logging.ERROR if result.failed else logging.INFO,
                   "Expiry check completed: %s", asdict(result))
    except Exception as error:
        logger.error("Expiry job could not run (%s)", type(error).__name__)


@asynccontextmanager
async def reminder_lifespan(app):
    settings = ReminderSettings.from_environment()
    if not settings.enabled:
        logger.warning("Trial reminders are disabled: REMINDERS_ENABLED is false.")
        yield
        return
    scheduler = BackgroundScheduler(
        timezone=REMINDER_TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    scheduler.add_job(
        scheduled_check, CronTrigger(hour=9, minute=0, timezone=REMINDER_TIMEZONE),
        id="daily-trial-reminders",
    )
    # Re-check today's milestones after a restart; last_notified_at prevents repeats.
    scheduler.add_job(
        scheduled_check, "date", run_date=datetime.now(timezone.utc),
        id="startup-trial-reminders",
    )
    scheduler.start()
    app.state.reminder_scheduler = scheduler
    logger.warning(
        "Daily trial reminders scheduled for 09:00 Asia/Kolkata; delivery mode=%s. "
        "Reliable unattended operation requires always-on hosting.", settings.mode,
    )
    try:
        yield
    finally:
        await asyncio.to_thread(scheduler.shutdown, wait=True)
