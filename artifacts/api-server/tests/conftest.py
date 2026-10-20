"""Every test uses disposable SQLite data and a disabled real email transport."""

import os
from html.parser import HTMLParser
from secrets import token_urlsafe
from types import SimpleNamespace

from test_support import TEST_PASSWORD

# Override before app imports: never connect tests to a supplied development/prod DB.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SESSION_SECRET"] = token_urlsafe(48)
os.environ["NODE_ENV"] = "development"
os.environ["REMINDERS_ENABLED"] = "false"
os.environ["REMINDER_DELIVERY_MODE"] = "test"
os.environ["REMINDER_TEST_EMAIL"] = "allowed@example.com"
os.environ["RESEND_FROM_EMAIL"] = "StillFree <onboarding@resend.dev>"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services import mailer


class TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


@pytest.fixture(autouse=True)
def block_real_email(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tests must not launch a real email connector process.")

    monkeypatch.setattr(mailer, "subprocess", SimpleNamespace(
        run=forbidden,
        TimeoutExpired=__import__("subprocess").TimeoutExpired,
    ))
    monkeypatch.setenv("REMINDERS_ENABLED", "false")


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(sessions):
    def isolated_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as browser:
        yield browser
    app.dependency_overrides.clear()


@pytest.fixture
def token():
    def extract(response):
        parser = TokenParser()
        parser.feed(response.text)
        assert parser.token, "Expected a CSRF token in the form."
        return parser.token
    return extract


@pytest.fixture
def register(token):
    def signup(client, email="person@example.com", password=TEST_PASSWORD):
        csrf = token(client.get("/api/signup"))
        return client.post("/api/signup", data={
            "email": email, "password": password, "csrf_token": csrf,
        })
    return signup
