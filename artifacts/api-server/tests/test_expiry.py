from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.models import Service, User
from app.services.mailer import DeliveryError, ReminderSettings
from app.services.reminders import (
    REMINDER_TIMEZONE, reminder_message, run_expiry_check, should_notify,
)

NOW = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)
TODAY = NOW.astimezone(REMINDER_TIMEZONE).date()
SETTINGS = ReminderSettings(True, "test", "StillFree <onboarding@resend.dev>", "allowed@example.com")


def make_trial(sessions, days=7, email="allowed@example.com", **fields):
    with sessions.begin() as db:
        from sqlalchemy import select
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash="unused-test-hash")
            db.add(user)
            db.flush()
        service = Service(
            user_id=user.id, name="Example trial", category="Cloud",
            expires_on=TODAY + timedelta(days=days), **fields,
        )
        db.add(service)
        db.flush()
        return service.id


@pytest.mark.parametrize("days,expected", [(-1, False), (0, False), (1, True), (2, False), (6, False), (7, True), (8, False)])
def test_only_exact_seven_and_one_day_milestones_are_eligible(days, expected):
    trial = Service(expires_on=TODAY + timedelta(days=days))
    assert should_notify(trial, NOW) is expected


def test_job_sends_only_due_trials_to_their_owner(sessions):
    make_trial(sessions, days=7, price_after_trial=Decimal("199.50"), cancel_url="https://example.com/cancel")
    make_trial(sessions, days=1)
    make_trial(sessions, days=2)
    make_trial(sessions, days=-1)
    sender = Mock(return_value="accepted")
    result = run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender)
    assert result.candidates == 2 and result.sent == 2 and result.failed == 0
    assert all(call.args[0] == "allowed@example.com" for call in sender.call_args_list)
    assert any("₹199.50" in call.args[2] and "https://example.com/cancel" in call.args[2] for call in sender.call_args_list)


def test_repeat_job_does_not_send_the_same_alert_twice(sessions):
    trial_id = make_trial(sessions)
    sender = Mock(return_value="accepted")
    assert run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender).sent == 1
    repeat = run_expiry_check(SETTINGS, now=NOW + timedelta(hours=2), session_factory=sessions, send=sender)
    assert repeat.sent == 0 and repeat.already_notified == 1
    assert sender.call_count == 1
    with sessions() as db:
        assert db.get(Service, trial_id).last_notified_at is not None


def test_seven_day_alert_does_not_suppress_one_day_alert(sessions):
    make_trial(sessions)
    sender = Mock(return_value="accepted")
    run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender)
    result = run_expiry_check(SETTINGS, now=NOW + timedelta(days=6), session_factory=sessions, send=sender)
    assert result.sent == 1 and sender.call_count == 2
    assert "7 days" in sender.call_args_list[0].args[1]
    assert "1 day" in sender.call_args_list[1].args[1]
    assert sender.call_args_list[0].args[3] != sender.call_args_list[1].args[3]


def test_failure_is_not_marked_and_retry_uses_the_same_provider_key(sessions):
    trial_id = make_trial(sessions)
    sender = Mock(side_effect=DeliveryError("Simulated rejection"))
    assert run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender).failed == 1
    first_key = sender.call_args.args[3]
    with sessions() as db:
        assert db.get(Service, trial_id).last_notified_at is None
    sender.side_effect = None
    sender.return_value = "accepted"
    assert run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender).sent == 1
    assert sender.call_args.args[3] == first_key


def test_one_failed_email_does_not_stop_other_trials(sessions):
    make_trial(sessions, days=7)
    make_trial(sessions, days=1)
    sender = Mock(side_effect=[DeliveryError("Rejected"), "accepted"])
    result = run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender)
    assert (result.failed, result.sent) == (1, 1)


def test_test_mode_blocks_other_users_without_redirecting_their_data(sessions):
    trial_id = make_trial(sessions, email="another@example.com")
    sender = Mock()
    result = run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender)
    assert result.blocked == 1 and result.sent == 0
    sender.assert_not_called()
    with sessions() as db:
        assert db.get(Service, trial_id).last_notified_at is None


def test_dry_run_neither_sends_nor_marks(sessions):
    trial_id = make_trial(sessions)
    sender = Mock()
    result = run_expiry_check(SETTINGS, now=NOW, session_factory=sessions, send=sender, dry_run=True)
    assert result.dry_run and result.candidates == 1 and result.sent == 0
    sender.assert_not_called()
    with sessions() as db:
        assert db.get(Service, trial_id).last_notified_at is None


def test_indian_calendar_boundary_not_utc_calendar_controls_milestones():
    # 20:00 UTC is already the following day in India.
    now = datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)
    trial = Service(expires_on=TODAY + timedelta(days=2))
    assert should_notify(trial, now)
    trial.last_notified_at = now - timedelta(hours=1)
    assert not should_notify(trial, now)


@pytest.mark.parametrize("price,expected", [(None, "Not specified"), (Decimal("0"), "₹0.00"), (Decimal("123456.78"), "₹1,23,456.78")])
def test_message_includes_service_days_price_and_optional_link(price, expected):
    trial = Service(name="Sample AI", expires_on=TODAY + timedelta(days=1), price_after_trial=price)
    subject, text = reminder_message(trial, 1)
    assert "Sample AI" in subject and "1 day" in subject
    assert "Sample AI" in text and "1 day" in text and expected in text
    assert "Cancel or manage" not in text
    trial.cancel_url = "https://example.com/cancel"
    assert trial.cancel_url in reminder_message(trial, 7)[1]


def test_job_refuses_disabled_delivery_and_naive_clock(sessions):
    with pytest.raises(ValueError, match="Enable REMINDERS_ENABLED"):
        run_expiry_check(ReminderSettings(False, "test", "", ""), session_factory=sessions)
    with pytest.raises(ValueError, match="timezone-aware"):
        run_expiry_check(SETTINGS, now=NOW.replace(tzinfo=None), session_factory=sessions)
