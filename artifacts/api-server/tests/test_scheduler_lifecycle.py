"""Exercise enabled lifespans without starting a real scheduler, jobs, or email."""

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.jobs import scheduler


@pytest.fixture
def scheduler_harness(monkeypatch):
    monkeypatch.setenv("REMINDERS_ENABLED", "true")
    monkeypatch.setenv("REMINDER_DELIVERY_MODE", "test")
    monkeypatch.setenv("REMINDER_TEST_EMAIL", "allowed@example.com")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "StillFree <onboarding@resend.dev>")
    now = datetime(2030, 1, 1, 3, 29, 40, tzinfo=timezone.utc)
    instances = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return now

    class FakeScheduler:
        def __init__(self, **options):
            self.options = options
            self.jobs = {}
            self.events = []
            instances.append(self)

        def add_job(self, function, trigger, **options):
            job_id = options["id"]
            assert job_id not in self.jobs, "Duplicate reminder job registration"
            self.jobs[job_id] = SimpleNamespace(
                function=function, trigger=trigger, options=options,
            )
            self.events.append("add_job")

        def start(self):
            self.events.append("start")

        def shutdown(self, *, wait):
            self.events.append(("shutdown", wait))

    def forbidden_check(*args, **kwargs):
        pytest.fail("Lifecycle tests must not run reminder jobs or send emails.")

    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(scheduler, "run_expiry_check", forbidden_check)
    return SimpleNamespace(instances=instances, now=now)


def assert_reminder_jobs(fake, now):
    assert str(fake.options["timezone"]) == "Asia/Kolkata"
    assert fake.options["job_defaults"] == {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 3600,
    }
    assert set(fake.jobs) == {
        "daily-trial-reminders", "startup-trial-reminders",
    }
    daily = fake.jobs["daily-trial-reminders"]
    startup = fake.jobs["startup-trial-reminders"]
    assert daily.function is scheduler.scheduled_check
    assert startup.function is scheduler.scheduled_check
    assert isinstance(daily.trigger, CronTrigger)
    assert str(daily.trigger.timezone) == "Asia/Kolkata"
    assert {field.name: str(field) for field in daily.trigger.fields} == {
        "year": "*", "month": "*", "day": "*", "week": "*",
        "day_of_week": "*", "hour": "9", "minute": "0", "second": "0",
    }
    # 03:30 UTC is 09:00 Kolkata: check consecutive days without wall-clock waits.
    next_daily = datetime(2030, 1, 1, 3, 30, tzinfo=timezone.utc)
    assert daily.trigger.get_next_fire_time(None, now) == next_daily
    assert daily.trigger.get_next_fire_time(
        None, next_daily + timedelta(seconds=1),
    ) == next_daily + timedelta(days=1)
    assert startup.trigger == "date"
    assert startup.options["run_date"] == now
    assert startup.options["run_date"].tzinfo is timezone.utc


@pytest.mark.parametrize("body_raises", [False, True])
def test_enabled_lifespan_schedules_once_and_waits_for_shutdown(
    scheduler_harness, body_raises,
):
    app = SimpleNamespace(state=SimpleNamespace())

    async def exercise():
        expected = (
            pytest.raises(RuntimeError, match="synthetic lifespan failure")
            if body_raises else nullcontext()
        )
        with expected:
            async with scheduler.reminder_lifespan(app):
                assert len(scheduler_harness.instances) == 1
                fake = scheduler_harness.instances[0]
                assert app.state.reminder_scheduler is fake
                assert_reminder_jobs(fake, scheduler_harness.now)
                assert fake.events == ["add_job", "add_job", "start"]
                if body_raises:
                    raise RuntimeError("synthetic lifespan failure")

    asyncio.run(exercise())
    assert scheduler_harness.instances[0].events == [
        "add_job", "add_job", "start", ("shutdown", True),
    ]


def test_restart_creates_one_fresh_scheduler_with_the_same_two_job_ids(
    scheduler_harness,
):
    async def restart_twice():
        for index in range(2):
            app = SimpleNamespace(state=SimpleNamespace())
            async with scheduler.reminder_lifespan(app):
                assert len(scheduler_harness.instances) == index + 1
                fake = scheduler_harness.instances[index]
                assert app.state.reminder_scheduler is fake
                assert_reminder_jobs(fake, scheduler_harness.now)
                assert fake.events == ["add_job", "add_job", "start"]
            assert fake.events == [
                "add_job", "add_job", "start", ("shutdown", True),
            ]

    asyncio.run(restart_twice())
    first, second = scheduler_harness.instances
    assert first is not second
    assert first.jobs is not second.jobs
    assert first.jobs.keys() == second.jobs.keys()
