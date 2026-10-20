"""Exercise a cold app import and its real lifespan without database writes."""

import os
from pathlib import Path
from secrets import token_urlsafe
import subprocess
import sys
import textwrap

import pytest


API_ROOT = Path(__file__).resolve().parents[1]

# conftest imports app.main before tests run. A fresh interpreter is essential:
# reloading main alone would miss import-time work in its cached dependencies.
STARTUP_PROBE = textwrap.dedent("""
    import os
    from unittest.mock import patch

    from alembic import command
    from fastapi.testclient import TestClient
    from sqlalchemy import MetaData, Table
    from sqlalchemy.engine import Engine

    attempts = []

    def forbidden(operation):
        def fail(*args, **kwargs):
            attempts.append(operation)
            raise AssertionError("Startup must not invoke " + operation)
        return fail

    from contextlib import ExitStack

    with ExitStack() as guards:
        # No test schema is created. Refuse connections before any SQL (including
        # raw-driver DDL) can execute, even if a startup helper builds a new engine.
        # Record attempts as well as raising, so swallowed exceptions still fail.
        for name in ("connect", "raw_connection"):
            guards.enter_context(patch.object(
                Engine, name, forbidden("database " + name),
            ))
        for owner, names in (
            (MetaData, ("create_all", "drop_all")),
            (Table, ("create", "drop")),
        ):
            for name in names:
                guards.enter_context(patch.object(
                    owner, name, forbidden(owner.__name__ + "." + name),
                ))

        # Catch migration entry points even if they decide no SQL is needed.
        for name in ("upgrade", "downgrade", "stamp", "revision", "ensure_version"):
            guards.enter_context(patch.object(
                command, name, forbidden("alembic.command." + name),
            ))
        guards.enter_context(patch(
            "subprocess.Popen", forbidden("external command"),
        ))
        guards.enter_context(patch("os.system", forbidden("shell command")))

        from app import migrate
        guards.enter_context(patch.object(
            migrate, "main", forbidden("app.migrate.main"),
        ))

        # Keep the actual lifespan, but never start reminder delivery or a job.
        guards.enter_context(patch(
            "apscheduler.schedulers.background.BackgroundScheduler.start",
            forbidden("reminder scheduler"),
        ))
        assert os.environ["REMINDERS_ENABLED"] == "false"

        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/healthz")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        assert attempts == [], "Forbidden startup operations: " + repr(attempts)
    print("Cold import, startup, health check, and shutdown completed safely.")
""")


@pytest.mark.parametrize("node_env", ["development", "production"])
def test_startup_never_creates_alters_or_migrates_schema(node_env):
    result = subprocess.run(
        [sys.executable, "-c", STARTUP_PROBE],
        cwd=API_ROOT,
        env={
            **os.environ,
            # Never use a supplied application database, signing key, or mail
            # configuration. Connections and command-based transports are blocked.
            "DATABASE_URL": "sqlite://",
            "SESSION_SECRET": token_urlsafe(48),
            "NODE_ENV": node_env,
            "REMINDERS_ENABLED": "false",
            "REMINDER_DELIVERY_MODE": "test",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "shutdown completed safely." in result.stdout
