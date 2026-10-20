import json
import os
import re
import subprocess
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path


class DeliveryError(Exception):
    """A delivery failure with a safe-to-log message, never credentials."""


@dataclass(frozen=True)
class ReminderSettings:
    enabled: bool
    mode: str
    from_email: str
    test_email: str

    @classmethod
    def from_environment(cls):
        enabled = os.environ.get("REMINDERS_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("REMINDERS_ENABLED must be true or false.")
        settings = cls(
            enabled=enabled == "true",
            mode=os.environ.get("REMINDER_DELIVERY_MODE", "test"),
            from_email=os.environ.get("RESEND_FROM_EMAIL", "").strip(),
            test_email=os.environ.get("REMINDER_TEST_EMAIL", "").strip().lower(),
        )
        if settings.mode not in {"test", "live"}:
            raise ValueError("REMINDER_DELIVERY_MODE must be test or live.")
        if settings.enabled:
            sender = parseaddr(settings.from_email)[1]
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
                raise ValueError("RESEND_FROM_EMAIL must be a valid sender address.")
            if settings.mode == "test" and not settings.test_email:
                raise ValueError("Test delivery requires REMINDER_TEST_EMAIL.")
            if settings.mode == "live" and sender.endswith("@resend.dev"):
                raise ValueError("Live delivery requires your own verified sender domain.")
        return settings

    def allows(self, recipient: str) -> bool:
        # Never redirect someone else's trial details to the test recipient.
        return self.mode == "live" or recipient.strip().lower() == self.test_email


class ResendMailer:
    def __init__(self, settings: ReminderSettings):
        self.settings = settings

    def send(self, recipient: str, subject: str, text: str, key: str) -> str:
        if not self.settings.enabled:
            raise DeliveryError("Reminder delivery is disabled.")
        if not self.settings.allows(recipient):
            raise DeliveryError("Recipient blocked by test-only delivery.")
        payload = {
            "from": self.settings.from_email,
            "to": recipient,
            "subject": subject.replace("\r", " ").replace("\n", " "),
            "text": text,
        }
        bridge = Path(__file__).resolve().parents[2] / "resend-proxy.mjs"
        try:
            result = subprocess.run(
                ["node", str(bridge)],
                input=json.dumps({"payload": payload, "idempotencyKey": key}),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            raise DeliveryError("Email connector unavailable or timed out.") from error
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise DeliveryError("Invalid email connector response.") from error
        if result.returncode or not response.get("id"):
            status = response.get("status")
            suffix = f" (HTTP {status})" if isinstance(status, int) else ""
            raise DeliveryError(f"Resend did not accept the email{suffix}.")
        return response["id"]
