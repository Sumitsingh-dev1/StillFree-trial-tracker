import re

from pydantic import BaseModel, field_validator


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_and_validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) > 255 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized
        ):
            raise ValueError("Enter a valid email address.")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        encoded_length = len(value.encode("utf-8"))
        if len(value) < 8:
            raise ValueError("Use a password with at least 8 characters.")
        if encoded_length > 72:
            raise ValueError("Password must be 72 bytes or fewer.")
        return value
