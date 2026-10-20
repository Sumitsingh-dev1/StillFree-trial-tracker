#!/bin/bash
set -euo pipefail

# Replit runs this hook against development after a merge, never on publish.
# Refuse use in a published deployment using Replit's documented app marker.
# NODE_ENV is a runtime mode, and the development hook runner can inherit
# REPLIT_ENVIRONMENT=production. Neither reliably identifies a published app.
if [[ "${REPLIT_DEPLOYMENT:-}" == "1" ]]; then
  echo "Post-merge setup is development-only; REPLIT_DEPLOYMENT marks this as a deployment." >&2
  exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.."
pnpm install --frozen-lockfile
uv sync --frozen

# SQLAlchemy owns StillFree's schema. The empty scaffold Drizzle schema must
# never be pushed: it can propose dropping the Python tables.
# create_all only creates missing tables; it never drops or alters existing data.
# The migration command first checks existing model columns, failing this hook
# before any DDL if a separately reviewed development migration is needed.
PYTHONPATH=artifacts/api-server uv run --no-sync python -m app.migrate
