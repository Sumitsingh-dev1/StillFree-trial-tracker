"""Exercise the real app setup in production without changing the shared test app."""

from http.cookies import SimpleCookie
from pathlib import Path
import runpy
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from test_support import TEST_PASSWORD

from app.database import get_db
from app.main import app as development_app
from app.services.auth import register_user


@pytest.fixture
def production_client(monkeypatch, sessions):
    # main.py reads NODE_ENV at import time. Execute it in a fresh namespace,
    # rather than reloading app.main and leaking production settings to other tests.
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SESSION_SECRET", token_urlsafe(48))
    monkeypatch.setenv("REMINDERS_ENABLED", "false")
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    production_app = runpy.run_path(str(main_path))["app"]
    assert production_app is not development_app

    def isolated_db():
        with sessions() as db:
            yield db

    production_app.dependency_overrides[get_db] = isolated_db
    try:
        # The inherited autouse fixture also prevents real email subprocesses.
        with TestClient(
            production_app, base_url="https://testserver", follow_redirects=False,
        ) as browser:
            assert not hasattr(production_app.state, "reminder_scheduler")
            yield browser
    finally:
        production_app.dependency_overrides.clear()


def session_cookie(response):
    headers = response.headers.get_list("set-cookie")
    cookie = SimpleCookie()
    for header in headers:
        cookie.load(header)
    assert set(cookie) == {"stillfree_session"}
    session = cookie["stillfree_session"]
    assert session["secure"]
    assert session["httponly"]
    assert session["samesite"].lower() == "lax"
    assert session["path"] == "/"
    return session


@pytest.fixture
def account(sessions):
    with sessions() as db:
        register_user(db, "person@example.com", TEST_PASSWORD)


def login(browser, token):
    page = browser.get("/api/login")
    assert page.status_code == 200
    session_cookie(page)
    response = browser.post("/api/login", data={
        "email": "person@example.com",
        "password": TEST_PASSWORD,
        "csrf_token": token(page),
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/api/dashboard"
    assert session_cookie(response).value
    return response


def test_production_login_session_and_logout_over_https(production_client, account, token):
    browser = production_client
    anonymous = browser.get("/api/dashboard")
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/api/login"
    login(browser, token)

    # Rely on the browser's cookie jar, not a manually supplied Cookie header.
    for _ in range(2):
        dashboard = browser.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert "stillfree_session=" in dashboard.request.headers["cookie"]
        # Unchanged sessions need not be reissued on every response.
        if dashboard.headers.get_list("set-cookie"):
            session_cookie(dashboard)

    logout = browser.post("/api/logout", data={"csrf_token": token(dashboard)})
    assert logout.status_code == 303
    assert logout.headers["location"] == "/api/login"
    expired = session_cookie(logout)
    assert expired["expires"] == "Thu, 01 Jan 1970 00:00:00 GMT"
    assert browser.cookies.get("stillfree_session") is None
    denied = browser.get("/api/dashboard")
    assert denied.status_code == 303
    assert denied.headers["location"] == "/api/login"


def test_production_signup_sets_secure_authenticated_session(production_client, register):
    response = register(production_client)
    assert response.status_code == 303
    assert response.headers["location"] == "/api/dashboard"
    assert session_cookie(response).value
    assert production_client.get("/api/dashboard").status_code == 200


def test_production_secure_session_is_not_sent_over_http(production_client, account, token):
    browser = production_client
    login(browser, token)

    denied = browser.get("http://testserver/api/dashboard")
    assert "cookie" not in denied.request.headers
    assert denied.status_code == 303
    assert denied.headers["location"] == "/api/login"
    # An insecure request must not destroy the HTTPS session.
    assert browser.get("/api/dashboard").status_code == 200
