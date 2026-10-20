from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

CATEGORIES = ("Cloud", "Streaming", "Software/AI", "Fitness", "Other")


class ServiceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: Literal["Cloud", "Streaming", "Software/AI", "Fitness", "Other"]
    expires_on: date
    price_after_trial: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    cancel_url: HttpUrl | None = Field(default=None, max_length=2048)

    @field_validator("name", "category", "expires_on", mode="before")
    @classmethod
    def trim_required(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("price_after_trial", "cancel_url", mode="before")
    @classmethod
    def blank_optional(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value
