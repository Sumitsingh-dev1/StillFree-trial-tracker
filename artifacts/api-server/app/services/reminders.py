import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Service, User
from app.services.mailer import DeliveryError, ReminderSettings, ResendMailer
from app.views import inr

logger = logging.getLogger(__name__)
REMINDER_TIMEZONE = ZoneInfo("Asia/Kolkata")


@dataclass
class ExpiryCheckResult:
    candidates: int = 0
    sent: int = 0
    blocked: int = 0
    already_notified: int = 0
    failed: int = 0
    dry_run: bool = False


def should_notify(service: Service, now: datetime) -> bool:
    today = now.astimezone(REMINDER_TIMEZONE).date()
    if (service.expires_on - today).days not in {7, 1}:
        return False
    if service.last_notified_at is None:
        return True
    notified = service.last_notified_at
    if notified.tzinfo is None:
        notified = notified.replace(tzinfo=timezone.utc)
    return notified.astimezone(REMINDER_TIMEZONE).date() < today


def reminder_message(service: Service, days_left: int) -> tuple[str, str]:
    day_word = "day" if days_left == 1 else "days"
    subject = f"StillFree: {service.name} expires in {days_left} {day_word}"
    price = inr(service.price_after_trial) if service.price_after_trial is not None else "Not specified"
    text = (
        f"Your {service.name} trial expires in {days_left} {day_word}.\n\n"
        f"Expiry date: {service.expires_on.isoformat()}\n"
        f"Price charged after the trial (INR): {price}\n"
    )
    if service.cancel_url:
        text += f"Cancel or manage your trial: {service.cancel_url}\n"
    text += "\nReview the service's terms before the trial ends to avoid an unwanted charge.\n— StillFree"
    return subject, text


def run_expiry_check(
    settings: ReminderSettings | None = None,
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    send=None,
    dry_run: bool = False,
) -> ExpiryCheckResult:
    settings = settings or ReminderSettings.from_environment()
    if not settings.enabled:
        raise ValueError("Enable REMINDERS_ENABLED before running the expiry job.")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Reminder checks require a timezone-aware timestamp.")
    today = now.astimezone(REMINDER_TIMEZONE).date()
    due_dates = [today + timedelta(days=7), today + timedelta(days=1)]
    result = ExpiryCheckResult(dry_run=dry_run)
    send = send or ResendMailer(settings).send
    with session_factory() as db:
        ids = list(db.scalars(select(Service.id).where(Service.expires_on.in_(due_dates))))
    result.candidates = len(ids)
    for service_id in ids:
        try:
            did_send = False
            with session_factory.begin() as db:
                # Serialize sends per trial, even if startup and daily jobs overlap.
                trial = db.scalar(
                    select(Service).where(Service.id == service_id)
                    .with_for_update(skip_locked=True)
                )
                if trial is None or not should_notify(trial, now):
                    result.already_notified += 1
                    continue
                owner = db.get(User, trial.user_id)
                if owner is None or not settings.allows(owner.email):
                    result.blocked += 1
                    continue
                if dry_run:
                    continue
                days_left = (trial.expires_on - today).days
                subject, text = reminder_message(trial, days_left)
                # Stable provider key covers acceptance followed by a lost DB commit.
                key = f"stillfree/trial/{trial.id}/{trial.expires_on.isoformat()}/{days_left}"
                send(owner.email, subject, text, key)
                trial.last_notified_at = now
                did_send = True
            if did_send:
                result.sent += 1
        except Exception as error:
            result.failed += 1
            # Avoid logging message content, addresses, or database connection strings.
            reason = str(error) if isinstance(error, DeliveryError) else type(error).__name__
            logger.error("Reminder failed for trial %s (%s)", service_id, reason)
    return result
