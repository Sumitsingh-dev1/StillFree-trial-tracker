// This scaffold does not own StillFree's schema. Never load a database client here.
console.error(
  "StillFree scaffold schema writes are disabled: SQLAlchemy owns the database. " +
    "The empty Drizzle schema could delete Python-owned tables. " +
    "For additive development setup only, run from the repository root: " +
    "PYTHONPATH=artifacts/api-server uv run python -m app.migrate. " +
    "Do not use this command for production migrations.",
);
process.exit(1);
