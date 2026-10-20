"""Scheduled callback failures must be logged without preventing the next run."""

from dataclasses import asdict
import logging
from unittest.mock import Mock, call

import pytest

from app.jobs import scheduler
from app.services.reminders import ExpiryCheckResult


@pytest.fixture
def reminder_runner(monkeypatch):
    runner = Mock()
    monkeypatch.setattr(scheduler, "run_expiry_check", runner)

    def forbidden_scheduler(*args, **kwargs):
        pytest.fail("Callback tests must not construct a real scheduler.")

    monkeypatch.setattr(scheduler, "BackgroundScheduler", forbidden_scheduler)
    return runner


def assert_next_check_succeeds(reminder_runner, caplog, expected):
    caplog.clear()
    assert scheduler.scheduled_check() is None
    assert reminder_runner.call_args_list == [call(), call()]
    records = [
        record for record in caplog.records
        if record.name == scheduler.logger.name
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].msg == "Expiry check completed: %s"
    assert records[0].args == asdict(expected)


@pytest.mark.parametrize("result, expected_level", [
    pytest.param(
        ExpiryCheckResult(candidates=3, sent=1, blocked=1, already_notified=1),
        logging.INFO, id="successful-check",
    ),
    pytest.param(
        ExpiryCheckResult(candidates=4, sent=1, blocked=1, failed=2),
        logging.ERROR, id="delivery-failures",
    ),
])
def test_scheduled_check_logs_results_and_allows_another_run(
    reminder_runner, caplog, result, expected_level,
):
    recovery = ExpiryCheckResult(candidates=1, sent=1)
    reminder_runner.side_effect = [result, recovery]
    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        assert scheduler.scheduled_check() is None
        reminder_runner.assert_called_once_with()
        records = [
            record for record in caplog.records
            if record.name == scheduler.logger.name
        ]
        assert len(records) == 1
        assert records[0].levelno == expected_level
        assert records[0].msg == "Expiry check completed: %s"
        assert records[0].args == asdict(result)
        assert_next_check_succeeds(reminder_runner, caplog, recovery)


@pytest.mark.parametrize("exception_type", [RuntimeError, ValueError])
def test_scheduled_check_logs_service_exception_without_escaping_and_recovers(
    reminder_runner, caplog, exception_type,
):
    private_message = "synthetic-private-message-that-must-not-be-logged"
    recovery = ExpiryCheckResult(candidates=1, sent=1)
    reminder_runner.side_effect = [exception_type(private_message), recovery]
    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        # An escaping exception fails the test before these assertions.
        assert scheduler.scheduled_check() is None
        reminder_runner.assert_called_once_with()
        records = [
            record for record in caplog.records
            if record.name == scheduler.logger.name
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.ERROR
        assert records[0].getMessage() == (
            f"Expiry job could not run ({exception_type.__name__})"
        )
        assert records[0].exc_info is None
        assert private_message not in caplog.text
        assert_next_check_succeeds(reminder_runner, caplog, recovery)
