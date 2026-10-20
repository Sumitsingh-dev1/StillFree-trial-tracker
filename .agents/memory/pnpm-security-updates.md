---
name: pnpm transitive security updates
description: Verify resolved dependency versions rather than assuming a successful named update refreshed all nested dependencies.
---

A successful named recursive pnpm update can leave vulnerable transitive resolutions beneath unchanged direct dependencies, even with an infinite inspection depth.

**Why:** Updating selected vulnerable package names refreshed only some paths; updating their direct parents refreshed additional paths, while an unchanged latest parent still retained one older nested resolution.

**How to apply:** Audit the resolved lockfile after updates. Prefer compatible direct-parent updates; when the latest parent still retains an affected dependency, use a compatible patched-version override and confirm a frozen install succeeds. Preserve minimum-release-age protections.
