---
name: Python web artifact registration
description: Why a web bootstrap can register an existing server-rendered Python app without a React runtime
---

Treat artifact kind as publishing/preview registration, not a required UI framework.
An API artifact's kind cannot be changed; the supported web bootstrap can instead
be used to obtain a new web identity, then its generated service configuration
replaced through validation with the existing Python commands.

**Why:** The available artifact creation flow has no Python-web bootstrap, and
API/design-only projects are not recognized as publishable. Creating a React UI
would violate the project's Python/Jinja2 requirement.

**How to apply:** Keep only the registration and service configuration from the
web bootstrap, remove unused frontend scaffolding/dependencies, and transfer
service ownership rather than starting a second copy of the scheduler.
