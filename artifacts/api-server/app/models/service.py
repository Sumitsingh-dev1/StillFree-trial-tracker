from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("price_after_trial >= 0", name="services_nonnegative_price"),
        CheckConstraint(
            "category IN ('Cloud', 'Streaming', 'Software/AI', 'Fitness', 'Other')",
            name="services_valid_category",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(30))
    expires_on: Mapped[date] = mapped_column(Date, index=True)
    price_after_trial: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cancel_url: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
