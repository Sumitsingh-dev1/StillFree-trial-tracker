# StillFree

Forgotten free trials can turn into unwanted charges. StillFree keeps trial end dates, potential charges, and cancellation links together, with reminders before a trial ends.

## MVP features

- Email/password sign-up, login, and logout with bcrypt-hashed passwords.
- Private trial tracking: name, category, expiry date, optional INR price, and optional cancellation link.
- Add, edit, and delete only your own trials.
- Dashboard ordered by soonest expiry, with a category filter.
- Textual expiry badges: red below 3 days, yellow from 3–6 days, green from 7 days onward. Expired trials remain visible and are labeled.
- Account-wide **money at risk**: known prices for trials expiring today or later.
- Daily reminders at 7 days and 1 day before expiry, including the service name, days left, price, and cancellation link when supplied.

**Current email mode:** test-only. Only reminders belonging to the configured Resend account email can be sent. Other users are skipped without being marked notified; their information is never redirected to the test recipient.

## Tech stack

Python 3.13, FastAPI, SQLAlchemy, PostgreSQL, Jinja2, bcrypt, APScheduler, and pytest. The application renders HTML on the server; **StillFree does not use React**.

The surrounding Replit project uses a pnpm workspace. A small Node helper calls the official Replit connectors SDK for secure Resend delivery because the advertised Python connector package was unavailable. App, database, and reminder logic remain Python.

## How to run

### 1. Install dependencies

From the repository root, using Python 3.13+, Node.js 24, pnpm, and uv:

```sh
pnpm install --frozen-lockfile
uv sync --frozen
```

### 2. Configure the environment

On Replit, use the built-in PostgreSQL database and put sensitive configuration in **Secrets**, never in source files or commits.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL URL, supplied by the Replit database |
| `SESSION_SECRET` | A strong random secret for signing login cookies; store in Secrets |
| `PORT` | HTTP port, supplied by the managed Replit workflow |
| `NODE_ENV` | Set to `production` when published to require secure cookies |
| `REMINDERS_ENABLED` | `true` enables the scheduler; `false` explicitly disables it |
| `REMINDER_DELIVERY_MODE` | `test` initially; `live` only after choosing to enable verified-domain delivery |
| `REMINDER_TEST_EMAIL` | Your Resend account email, required for test mode |
| `RESEND_FROM_EMAIL` | Test mode: `StillFree <onboarding@resend.dev>`; live mode: an address on your verified domain |

Connect **Resend** through Replit Integrations. Its API key is securely injected by the connector: the application never stores or prints it, and no key belongs in this repository. This implementation uses the connected integration rather than a separate `RESEND_API_KEY` variable.

### 3. Initialize the development schema

```sh
PYTHONPATH=artifacts/api-server uv run python -m app.migrate
```

This development-only command first checks existing model columns for missing
columns, different types (including string length, numeric precision and, on
PostgreSQL, timestamp timezone), and nullability differences. If any mismatch
exists, it exits nonzero with table/column details and guidance before making any
schema changes; the merge hook cannot report the schema as ready. Prepare a
separately reviewed, data-preserving development migration and rerun the hook.
Do not drop tables or reset data to clear the check.

When those checks pass, it creates missing tables from the SQLAlchemy models.
Extra legacy tables/columns are preserved. This is additive initial setup, not a
general migration engine: it does not alter existing columns or constraints.
It checks required columns, unique email guarantees (constraints or non-partial
unique indexes), foreign keys, and the model's price/category check constraints.
Missing or incompatible safeguards stop setup without changing rows; prepare a
reviewed, data-preserving development migration rather than resetting tables.
Non-unique indexes and defaults are not compared. The application does **not** run schema changes during
startup. The command refuses `REPLIT_DEPLOYMENT=1`; do not use it as a production
startup hook or point it at a production database.

After task merges, `scripts/post-merge.sh` installs the locked pnpm and Python
dependencies and runs this same additive setup against the development database.
It runs without prompts, can be repeated safely, and refuses published deployments
(`REPLIT_DEPLOYMENT=1`). Never point it at a production connection string.
The retained `lib/db` package is an unused scaffold with an empty Drizzle schema;
its `push` and `push-force` commands are disabled and exit nonzero without database
access. The unused Drizzle CLI is not installed; its retained config also refuses
schema writes if the CLI is supplied separately, because the empty schema could
delete Python-owned tables. Workspace imports remain available;
there is no force flag or environment override for enabling scaffold schema writes.
Existing-column changes require a separately reviewed development schema change;
this hook intentionally does not reconcile or remove existing tables or columns.

### 4. Start the app

In Replit, use Run to start `artifacts/stillfree: web`. This is the sole
StillFree server workflow and supplies the configured port. It runs the existing
Python entrypoint through:

```sh
pnpm --filter @workspace/api-server run dev
```

Outside Replit, supply a port yourself:

```sh
PORT=8080 pnpm --filter @workspace/api-server run dev
```

Open `/`; it redirects to the server-rendered sign-in page at `/api/login`. Trial pages are also served under `/api/`.

## Reminder operations

The scheduler starts with FastAPI, runs daily at **09:00 Asia/Kolkata**, and re-checks today's milestones after a restart.

Inspect due trials without sending or changing notification timestamps:

```sh
PYTHONPATH=artifacts/api-server uv run python -m app.jobs.expiry --dry-run
```

Run a real check, or retry failed alerts on the same milestone day:

```sh
PYTHONPATH=artifacts/api-server uv run python -m app.jobs.expiry
```

The command reports counts, not message bodies or recipient addresses, and exits nonzero if a send fails. Successful acceptance by Resend updates `last_notified_at`; failed or test-blocked sends do not. Resend acceptance is not itself a guarantee of inbox delivery.

### Hosting and live delivery

- The hosting target is configured for an **always-on Reserved VM**. Publishing is a separate user action; running a development preview does not keep the app available indefinitely.
- The publishable **StillFree** web artifact is registered in `artifacts/stillfree/`.
  Its validated configuration owns the only Python service, production build/run
  commands, and `/api/healthz` startup check. The original `artifacts/api-server/`
  artifact keeps its immutable API kind and Python source, but declares no services.
  There is no React UI, static build, proxy server, or second reminder scheduler.
- Root `/` and all `/api/*` pages and assets go directly to FastAPI without path
  rewrites. Sessions still use the `stillfree_session` cookie at `/`; production
  sets `NODE_ENV=production` for Secure, HTTP-only, SameSite=Lax cookies.
- When ready to publish, select StillFree in Publishing and retain **Reserved VM**
  hosting. Confirm `DATABASE_URL`, the existing `SESSION_SECRET`, the Resend
  integration, and reminder settings are available in the published environment.
  The build installs locked Node/Python dependencies; startup runs one Uvicorn
  process without reload or extra workers. Do not add a separate reminder process.
  Publishing applies managed database schema changes; startup does not run DDL.
- Do not rely on an idle Autoscale instance for this in-process daily scheduler.
- Checks target the exact 7-day and 1-day dates. If the app is offline for an entire milestone day, that alert is missed.
- For general-user sending, first verify a domain in Resend and choose to enable live delivery. Then configure `REMINDER_DELIVERY_MODE=live` and a `RESEND_FROM_EMAIL` on that domain. Keep the current test-only configuration until then.

## Tests

From the repository root:

```sh
uv run pytest -q
```

Tests use a fresh in-memory SQLite database per test, override database dependencies, disable the scheduler, and block real email subprocesses. They do not read or mutate your application database, and do not send emails.

Coverage includes password hashing, normalized/duplicate emails, credential validation, login/logout, session persistence, CSRF, anonymous access, cross-account trial isolation, exact expiry milestones, Indian calendar boundaries, duplicate suppression, the later 1-day reminder, failure retries, optional/zero prices, test-only recipient restrictions, dry-run behavior, and the mocked email transport.

SQLite does not exercise PostgreSQL row-lock behavior. Provider delivery and production hosting are outside this isolated unit/route suite.

## Structure

```text
artifacts/api-server/
├── run.py                  # Uvicorn entrypoint
├── resend-proxy.mjs         # Secure Resend connector bridge
├── app/
│   ├── main.py             # FastAPI, sessions, scheduler lifecycle
│   ├── database.py         # SQLAlchemy connection/session setup
│   ├── migrate.py          # Explicit development schema initialization
│   ├── models/             # Users and services
│   ├── schemas/            # Form input validation
│   ├── routers/            # Auth, dashboard, and private trial routes
│   ├── services/           # Auth, trial, reminder, and mail logic
│   ├── jobs/               # Daily scheduler and one-shot expiry command
│   ├── templates/          # Jinja2 HTML pages
│   └── static/             # Styles
└── tests/                  # Isolated pytest suite
```

## Design decisions

- **Server rendering:** Jinja2 forms and redirects keep the MVP simple, without a client application framework.
- **Signed sessions:** HTTP-only, SameSite cookies store the user ID; passwords are bcrypt hashes. Form submissions require a per-session CSRF token.
- **Ownership on the server:** Trial lookups constrain both trial ID and authenticated user ID. Another user's record returns 404.
- **Date-only expiry:** One Asia/Kolkata calendar boundary keeps badges and reminders consistent. A trial expiring today is active through that date.
- **Decimal money:** Prices use fixed-precision decimals. A missing price is unknown, not zero; no monthly billing cadence is assumed. Category filtering does not change the account-wide total.
- **Duplicate prevention:** `last_notified_at` suppresses repeated daily checks. PostgreSQL row locks serialize concurrent sends, and stable Resend idempotency keys protect uncertain retries.
- **No startup DDL:** Development schema setup is explicit; app startup never creates or alters production tables.
