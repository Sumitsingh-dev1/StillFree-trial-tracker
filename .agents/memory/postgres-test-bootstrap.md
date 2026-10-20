---
name: PostgreSQL test bootstrap
description: Disposable PostgreSQL bootstrap failures can obscure otherwise passing application tests.
---

Treat disposable PostgreSQL bootstrap failures separately from application regressions. A timed-out `initdb` parent can leave bootstrap children writing to its temporary directory, so cleanup may mask the original timeout with a directory-not-empty error.

**Why:** Disposable cluster initialization intermittently exceeded the fixture timeout during authentication regression verification, while isolated SQLite authentication tests passed. In-memory database storage allowed one full run to pass but also timed out on a later run, so storage location alone is not a reliable fix.

**How to apply:** Preserve the original bootstrap diagnostic and clean up only processes owned by the test fixture before removing its directory. Temporary database storage and executable test-helper storage may need different locations; do not move executable helpers onto a non-executable filesystem.
