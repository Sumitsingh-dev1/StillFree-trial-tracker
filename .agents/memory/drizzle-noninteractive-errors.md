---
name: Non-interactive Drizzle errors
description: A misleading successful exit when Drizzle push needs destructive-change confirmation
---

Do not treat a zero exit status from a non-interactive Drizzle push as proof that
schema setup succeeded.

**Why:** On an isolated PostgreSQL reproduction, the installed Drizzle CLI proposed
deleting populated SQLAlchemy tables because the scaffold schema was empty. With
stdin closed and output captured, it printed “Interactive prompts require a TTY
terminal” but exited with status 0. No tables were deleted, yet setup appeared
successful to the caller.

**How to apply:** When diagnosing automated schema setup, inspect command output
and verify resulting schema/data, not only the shell exit status. Never resolve
this prompt by forcing the empty scaffold schema onto the Python-owned database.
