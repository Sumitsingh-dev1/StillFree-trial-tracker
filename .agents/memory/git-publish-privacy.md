---
name: Safe Git publication
description: Keep private environment settings out of GitHub snapshots and previously committed history.
---

**Rule:** Treat values in `.replit` shared environment settings as versioned project content. Store private recipients and credentials in Replit Secrets. Before publishing, inspect the outgoing snapshot and its history; `.gitignore` only excludes files from future snapshots and does not remove existing commits.

**Why:** Removing a private value from the current config does not erase copies in older commits. A regular push includes the branch's reachable history, even when today's working tree is clean.

**How to apply:** When past commits contain private config, publish a clean root snapshot and keep the original history on a local-only branch. Push only the clean branch; never publish its backup.
