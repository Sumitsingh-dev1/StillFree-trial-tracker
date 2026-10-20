"""Signing-key changes invalidate old sessions without changing shared app state."""

from contextlib import ExitStack
from pathlib import Path
import runpy
from secrets import token_urlsafe

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_support import TEST_PASSWORD

from app.database import get_db
from app.main import app as shared_app
from app.models import Service


def test_replacing_signing_key_rejects_old_login_cookie(
    monkeypatch, sessions, register, token,
):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("REMINDERS_ENABLED", "false")
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"

    def isolated_db():
        with sessions() as db:
            yield db

    with ExitStack() as stack:
        def fresh_browser(signing_key):
            monkeypatch.setenv("SESSION_SECRET", signing_key)
            isolated_app = runpy.run_path(str(main_path))["app"]
            assert isolated_app is not shared_app
            isolated_app.dependency_overrides[get_db] = isolated_db
            stack.callback(isolated_app.dependency_overrides.clear)
            browser = stack.enter_context(TestClient(
                isolated_app, base_url="https://testserver",
                follow_redirects=False,
            ))
            assert not hasattr(isolated_app.state, "reminder_scheduler")
            return browser

        original = fresh_browser(token_urlsafe(48))
        rotated = fresh_browser(token_urlsafe(48))
        assert original.app is not rotated.app

        assert register(original).status_code == 303
        created = original.post("/api/services", data={
            "name": "Private rotation test trial",
            "category": "Cloud",
            "expires_on": "2030-01-01",
            "price_after_trial": "125.50",
            "cancel_url": "",
            "csrf_token": token(original.get("/api/services/new")),
        })
        assert created.status_code == 303
        with sessions() as db:
            trial_id = db.scalar(select(Service.id))
        assert trial_id is not None

        paths = (
            "/api/dashboard",
            "/api/services/new",
            f"/api/services/{trial_id}/edit",
        )
        for path in paths:
            assert original.get(path).status_code == 200
        old_cookie = original.cookies.get("stillfree_session")
        assert old_cookie

        # Replay the real authenticated cookie unchanged, not an absent cookie
        # or an anonymous replacement issued by a previous rejected request.
        for path in paths:
            rotated.cookies.clear()
            response = rotated.get(path, headers={
                "Cookie": f"stillfree_session={old_cookie}",
            })
            assert response.request.headers["cookie"] == (
                f"stillfree_session={old_cookie}"
            )
            assert response.status_code == 303
            assert response.headers["location"] == "/api/login"
            assert "Private rotation test trial" not in response.text

        # The account and private trial still work with a newly issued session:
        # rejection above must be due to the old key, not missing database data.
        login_page = rotated.get("/api/login")
        assert login_page.status_code == 200
        logged_in = rotated.post("/api/login", data={
            "email": "person@example.com",
            "password": TEST_PASSWORD,
            "csrf_token": token(login_page),
        })
        assert logged_in.status_code == 303
        assert logged_in.headers["location"] == "/api/dashboard"
        assert rotated.cookies.get("stillfree_session") != old_cookie
        for path in paths:
            assert rotated.get(path).status_code == 200
        assert "Private rotation test trial" in rotated.get(
            f"/api/services/{trial_id}/edit",
        ).text
