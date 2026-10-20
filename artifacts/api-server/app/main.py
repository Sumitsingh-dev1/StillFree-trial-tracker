import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.routers import auth, dashboard, trials
from app.routers.trials import LoginRequired
from app.jobs.scheduler import reminder_lifespan

session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    raise RuntimeError("SESSION_SECRET must be configured in Replit Secrets.")

app = FastAPI(title="StillFree", lifespan=reminder_lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="stillfree_session",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
    https_only=os.environ.get("NODE_ENV") == "production",
)

static_dir = Path(__file__).parent / "static"
app.mount("/api/static", StaticFiles(directory=static_dir), name="static")
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(trials.router)


@app.exception_handler(LoginRequired)
def require_login(request, error):
    return RedirectResponse("/api/login", status_code=303)


@app.get("/")
def root():
    return RedirectResponse("/api/", status_code=303)


@app.get("/api/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
