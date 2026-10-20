import json
from types import SimpleNamespace

import pytest

from app.services import mailer
from app.services.mailer import DeliveryError, ReminderSettings, ResendMailer

SETTINGS = ReminderSettings(True, "test", "StillFree <onboarding@resend.dev>", "allowed@example.com")


def test_transport_passes_json_over_stdin_without_credentials(monkeypatch):
    captured = {}

    def fake_run(command, **options):
        captured.update(command=command, **options)
        return SimpleNamespace(returncode=0, stdout='{"id":"test-message-id"}')

    monkeypatch.setattr(mailer.subprocess, "run", fake_run)
    assert ResendMailer(SETTINGS).send("allowed@example.com", "A\r\nsubject", "Text", "stable-key") == "test-message-id"
    message = json.loads(captured["input"])
    assert message["idempotencyKey"] == "stable-key"
    assert message["payload"]["subject"] == "A  subject"
    assert "Authorization" not in captured["input"]
    assert captured["command"][0] == "node"
    assert "allowed@example.com" not in " ".join(captured["command"])
    assert captured["timeout"] == 30


def test_transport_blocks_unapproved_test_recipient():
    with pytest.raises(DeliveryError, match="blocked"):
        ResendMailer(SETTINGS).send("another@example.com", "Subject", "Text", "key")


@pytest.mark.parametrize("returncode,stdout", [
    (1, '{"status":503,"error":"Provider error"}'),
    (0, "invalid JSON"),
    (0, "{}"),
])
def test_transport_rejects_failed_or_malformed_responses(monkeypatch, returncode, stdout):
    monkeypatch.setattr(mailer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout=stdout))
    with pytest.raises(DeliveryError):
        ResendMailer(SETTINGS).send("allowed@example.com", "Subject", "Text", "key")


def test_test_configuration_is_default_and_live_requires_verified_sender(monkeypatch):
    monkeypatch.setenv("REMINDERS_ENABLED", "true")
    assert ReminderSettings.from_environment().mode == "test"
    monkeypatch.setenv("REMINDER_DELIVERY_MODE", "live")
    with pytest.raises(ValueError, match="verified sender"):
        ReminderSettings.from_environment()
