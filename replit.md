# StillFree

StillFree tracks free trials and free tiers so users can avoid unwanted renewal charges.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the StillFree server
- `PYTHONPATH=artifacts/api-server uv run python -m app.migrate` — check existing model columns and key constraints, then create missing development tables; fails on drift without altering records
- `PYTHONPATH=artifacts/api-server uv run python -m app.jobs.expiry --dry-run` — inspect due reminders without sending or marking them
- `PYTHONPATH=artifacts/api-server uv run python -m app.jobs.expiry` — run one expiry check (sends eligible reminders)
- `uv run pytest -q` — run isolated authentication, expiry, and email-transport tests
- Required env: `DATABASE_URL`, `SESSION_SECRET`

## Stack

- Python 3.13, FastAPI, SQLAlchemy, Jinja2
- PostgreSQL via Replit's managed database
- pnpm workspace scaffold retained for the shared project services

## Where things live

- `artifacts/api-server/app/` — StillFree server, models, routes, schemas, services, and templates
- `artifacts/api-server/app/migrate.py` — explicit development schema setup

## Architecture decisions

- Passwords are stored as bcrypt hashes; login state is held in a signed, HTTP-only session cookie.
- Schema changes are applied explicitly to development, not during application startup.
- Post-merge setup installs locked dependencies and runs the additive Python development setup. Never push the empty scaffold Drizzle schema against StillFree: it can propose dropping Python-owned tables. Production schema changes remain owned by Publish.
- The web artifact in `artifacts/stillfree/` owns the single Python service and publishing configuration. Python/Jinja2 source remains in `artifacts/api-server/`; its original API registration has no services. The StillFree UI does not use React. Do not start a second app/scheduler process.
- Calendar calculations use Asia/Kolkata, providing one consistent date boundary for this INR-focused MVP. The reminder job must use the same boundary.
- Trials expiring today remain active through that date. Money at risk includes all unexpired trials, independently of the category filter; unknown prices do not contribute.
- Price is the amount charged after a trial, not an assumed monthly rate: the brief does not collect a billing cadence.
- Reminders run daily at 09:00 Asia/Kolkata and re-check today's milestones on startup. Always-on hosting is required; an idle autoscaling server cannot guarantee the daily job.
- A trial is marked notified only after Resend accepts the send. PostgreSQL row locks serialize concurrent checks; stable Resend idempotency keys cover retries after an uncertain response or commit.
- Email credentials are injected by the connected Replit Resend integration, never placed in source code.

## Product

- User accounts and private trial tracking are available, including add/edit/delete, expiry sorting and badges, category filtering, and INR money at risk.
- Daily 7-day and 1-day email reminders are implemented. Delivery is currently restricted to the configured test recipient; other owners are skipped without sending or marking them notified.
- The pytest suite covers authentication and expiry logic without touching the application database or sending email. See `README.md` for setup, operations, and design decisions.

## User preferences

- Build only the MVP in the attached brief, using Python, FastAPI, PostgreSQL, SQLAlchemy, and Jinja2. No React.
- Build in four stages and pause to show progress after each: setup/database/auth; trial CRUD/dashboard; expiry job/emails; pytest tests/README.
- The user chose test-only email delivery for now. Do not enable general-user sending without a verified sender domain and confirmation.

## Gotchas

- The advertised Python `replit-connectors` package was unavailable in the package registry. A small Node helper uses the already-installed official `@replit/connectors-sdk` for email only; all app/reminder logic remains Python.
- Reminder configuration: `REMINDERS_ENABLED`, `REMINDER_DELIVERY_MODE` (`test` or `live`), `RESEND_FROM_EMAIL`, and (in test mode) `REMINDER_TEST_EMAIL`. Keep addresses in runtime configuration, not source or memory.
- Test mode never redirects another user's details to the test address. It only sends reminders belonging to that recipient.
- A failed send leaves `last_notified_at` unchanged so the one-shot command can retry the same day's alerts. Failures are logged and make the one-shot command exit nonzero.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
