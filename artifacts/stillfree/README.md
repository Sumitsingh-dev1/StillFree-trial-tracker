# StillFree web registration

This artifact makes the existing FastAPI/Jinja2 application publishable. It has
no frontend bundle: its single `web` service runs `artifacts/api-server/run.py`
for development and production, serving `/` and `/api/*` without rewrites.

The Python source stays in `artifacts/api-server/`. Its original API artifact
retains its immutable identity but has no services. Do not add a second Python
service there: the FastAPI lifecycle owns the daily reminder scheduler.

Production build/run/health settings live in this directory's
`.replit-artifact/artifact.toml`; edit them only through the validated artifact
configuration tool. The repository's `.replit` keeps the always-on Reserved VM
target. See the root README for setup and publishing instructions.
