---
name: Post-merge runner environment
description: Why internal production-valued runner flags are not proof that a development setup hook is running on a published app.
---

Do not classify post-merge setup as production solely from `NODE_ENV` or `REPLIT_ENVIRONMENT`. Replit's documented published-app marker is `REPLIT_DEPLOYMENT=1`.

**Why:** A development merge's setup callback inherited `REPLIT_ENVIRONMENT=production`, even though the project environment API showed no configured override. A guard treating this internal runner flag as an app deployment repeatedly rejected valid development setup. Restricting the guard to the documented marker allowed setup and workflow reconciliation to complete.

**How to apply:** Preserve refusal when `REPLIT_DEPLOYMENT=1`. Do not unset that marker or change database credentials to force a setup run. When diagnosing a callback-only environment failure, distinguish inherited runner flags from project configuration rather than deleting unrelated environment variables.
