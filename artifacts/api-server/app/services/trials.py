from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Service
from app.schemas.service import ServiceInput


def today_date() -> date:
    # One calendar-day boundary for dashboard and the upcoming reminder job.
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def expiry_badge(days_left: int) -> tuple[str, str]:
    color = "red" if days_left < 3 else "yellow" if days_left < 7 else "green"
    if days_left < 0:
        label = "Expired"
    elif days_left == 0:
        label = "Expires today"
    else:
        label = f"{days_left} day{'s' if days_left != 1 else ''} left"
    return color, label


def dashboard_data(db: Session, user_id: int, category: str, today: date) -> dict:
    statement = select(Service).where(Service.user_id == user_id)
    if category:
        statement = statement.where(Service.category == category)
    trials = db.scalars(statement.order_by(Service.expires_on, Service.id)).all()
    rows = []
    for trial in trials:
        days_left = (trial.expires_on - today).days
        color, label = expiry_badge(days_left)
        rows.append({
            "id": trial.id,
            "name": trial.name,
            "category": trial.category,
            "expires_on": trial.expires_on,
            "price_after_trial": trial.price_after_trial,
            "cancel_url": trial.cancel_url,
            "days_left": days_left,
            "badge_class": color,
            "badge_label": label,
        })

    # Total is account-wide, independent of the display filter. Today is active.
    money_at_risk, active_count = db.execute(
        select(func.coalesce(func.sum(Service.price_after_trial), 0), func.count())
        .where(Service.user_id == user_id, Service.expires_on >= today)
    ).one()
    return {
        "services": rows,
        "money_at_risk": Decimal(money_at_risk),
        "active_count": active_count,
    }


def apply_input(service: Service, values: ServiceInput) -> None:
    # Changing an expiry date starts a new reminder schedule.
    if service.expires_on != values.expires_on:
        service.last_notified_at = None
    service.name = values.name
    service.category = values.category
    service.expires_on = values.expires_on
    service.price_after_trial = values.price_after_trial
    service.cancel_url = str(values.cancel_url) if values.cancel_url else None
