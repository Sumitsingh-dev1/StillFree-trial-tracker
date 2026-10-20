import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_support import TEST_PASSWORD

from app.main import app
from app.models import Service, User


def test_signup_normalizes_email_hashes_password_and_signs_in(client, register, sessions):
    response = register(client, email="  Person@Example.com  ")
    assert response.status_code == 303
    assert response.headers["location"] == "/api/dashboard"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    with sessions() as db:
        user = db.scalar(select(User))
        assert user.email == "person@example.com"
        assert user.created_at is not None
        assert user.password_hash != TEST_PASSWORD
        assert bcrypt.checkpw(TEST_PASSWORD.encode(), user.password_hash.encode())
    assert client.get("/api/dashboard").status_code == 200


def test_duplicate_email_is_case_insensitive(client, register, sessions):
    assert register(client).status_code == 303
    assert register(client, email="PERSON@EXAMPLE.COM").status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("email,password", [
    ("invalid", TEST_PASSWORD),
    ("person@example.com", TEST_PASSWORD[:4]),
    ("person@example.com", TEST_PASSWORD[:2]),
    ("person@example.com", TEST_PASSWORD * 3),
])
def test_signup_rejects_invalid_credentials(client, register, sessions, email, password):
    response = register(client, email=email, password=password)
    assert response.status_code == 400
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_login_logout_and_session_persistence(client, register, token):
    register(client)
    dashboard = client.get("/api/dashboard")
    assert client.post("/api/logout", data={"csrf_token": token(dashboard)}).status_code == 303
    assert client.get("/api/dashboard").headers["location"] == "/api/login"
    csrf = token(client.get("/api/login"))
    response = client.post("/api/login", data={
        "email": "PERSON@EXAMPLE.COM", "password": TEST_PASSWORD, "csrf_token": csrf,
    })
    assert response.status_code == 303
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/dashboard").status_code == 200  # retained signed cookie


@pytest.mark.parametrize("email,password", [
    ("person@example.com", TEST_PASSWORD + "-incorrect"),
    ("unknown@example.com", TEST_PASSWORD),
])
def test_login_rejects_wrong_credentials(client, register, token, email, password):
    register(client)
    client.post("/api/logout", data={"csrf_token": token(client.get("/api/dashboard"))})
    csrf = token(client.get("/api/login"))
    response = client.post("/api/login", data={
        "email": email, "password": password, "csrf_token": csrf,
    })
    assert response.status_code == 401
    assert "Email or password is incorrect." in response.text
    assert client.get("/api/dashboard").status_code == 303


@pytest.mark.parametrize("path", ["/api/dashboard", "/api/services/new", "/api/services/999/edit"])
def test_anonymous_users_cannot_open_private_pages(client, path):
    response = client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == "/api/login"


@pytest.mark.parametrize("path", ["/api/signup", "/api/login", "/api/logout", "/api/services"])
def test_mutations_reject_missing_csrf(client, path):
    assert client.post(path, data={"email": "person@example.com", "password": TEST_PASSWORD}).status_code == 403


def test_invalid_csrf_is_rejected(client, token):
    token(client.get("/api/signup"))
    assert client.post("/api/signup", data={
        "email": "person@example.com", "password": TEST_PASSWORD, "csrf_token": "forged",
    }).status_code == 403


@pytest.mark.parametrize("path", ["/api/signup", "/api/login"])
def test_auth_forms_reject_another_browsers_csrf(client, register, token, sessions, path):
    assert register(client).status_code == 303
    assert client.post("/api/logout", data={
        "csrf_token": token(client.get("/api/dashboard")),
    }).status_code == 303
    csrf = token(client.get(path))

    # Share the fixture's disposable database, but never its browser cookies.
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as other:
        other_csrf = token(other.get(path))
        assert csrf != other_csrf
        credentials = {
            "email": "new@example.com" if path == "/api/signup" else "person@example.com",
            "password": TEST_PASSWORD,
        }
        assert other.post(path, data={**credentials, "csrf_token": csrf}).status_code == 403
        assert token(other.get(path)) == other_csrf
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(User)) == 1

        response = other.post(path, data={**credentials, "csrf_token": other_csrf})
        assert response.status_code == 303
        assert response.headers["location"] == "/api/dashboard"
        assert other.get("/api/dashboard").status_code == 200
        with sessions() as db:
            expected_users = 2 if path == "/api/signup" else 1
            assert db.scalar(select(func.count()).select_from(User)) == expected_users


@pytest.mark.parametrize("token_source", ["another_browser", "before_logout"])
def test_protected_mutations_require_current_session_csrf(
    client, register, token, sessions, token_source,
):
    assert register(client).status_code == 303
    old_csrf = token(client.get("/api/dashboard"))

    # Both browsers use the same account so ownership checks cannot mask a CSRF bug.
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as other:
        browser = other if token_source == "another_browser" else client
        if token_source == "before_logout":
            assert client.post("/api/logout", data={"csrf_token": old_csrf}).status_code == 303

        login_csrf = token(browser.get("/api/login"))
        assert login_csrf != old_csrf
        assert browser.post("/api/login", data={
            "email": "person@example.com", "password": TEST_PASSWORD,
            "csrf_token": login_csrf,
        }).status_code == 303
        fresh_csrf = token(browser.get("/api/dashboard"))
        assert fresh_csrf not in (old_csrf, login_csrf)
        values = {
            "name": "Session-bound trial", "category": "Cloud", "expires_on": "2030-01-01",
            "price_after_trial": "125.50", "cancel_url": "",
        }

        assert browser.post("/api/services", data={
            **values, "csrf_token": old_csrf,
        }).status_code == 403
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(Service)) == 0
        assert browser.post("/api/services", data={
            **values, "csrf_token": fresh_csrf,
        }).status_code == 303
        with sessions() as db:
            trial = db.scalar(select(Service))
            assert trial.name == values["name"]
            trial_id = trial.id

        edit_path = f"/api/services/{trial_id}/edit"
        updated_values = {**values, "name": "Updated trial"}
        assert browser.post(edit_path, data={
            **updated_values, "csrf_token": old_csrf,
        }).status_code == 403
        with sessions() as db:
            assert db.get(Service, trial_id).name == values["name"]
        assert browser.post(edit_path, data={
            **updated_values, "csrf_token": fresh_csrf,
        }).status_code == 303
        with sessions() as db:
            assert db.get(Service, trial_id).name == updated_values["name"]

        delete_path = f"/api/services/{trial_id}/delete"
        assert browser.post(delete_path, data={"csrf_token": old_csrf}).status_code == 403
        with sessions() as db:
            assert db.get(Service, trial_id) is not None
        assert browser.post(delete_path, data={"csrf_token": fresh_csrf}).status_code == 303
        with sessions() as db:
            assert db.get(Service, trial_id) is None

        assert browser.post("/api/logout", data={"csrf_token": old_csrf}).status_code == 403
        assert browser.get("/api/dashboard").status_code == 200
        assert browser.post("/api/logout", data={"csrf_token": fresh_csrf}).status_code == 303
        assert browser.get("/api/dashboard").headers["location"] == "/api/login"


def test_private_trial_cannot_be_read_edited_or_deleted_by_another_user(client, register, token, sessions):
    register(client, email="owner@example.com")
    response = client.post("/api/services", data={
        "name": "Private trial", "category": "Cloud", "expires_on": "2030-01-01",
        "price_after_trial": "125.50", "cancel_url": "",
        "csrf_token": token(client.get("/api/services/new")),
    })
    assert response.status_code == 303
    with sessions() as db:
        trial_id = db.scalar(select(Service.id))
    client.post("/api/logout", data={"csrf_token": token(client.get("/api/dashboard"))})
    register(client, email="another@example.com")
    page = client.get("/api/dashboard")
    assert "Private trial" not in page.text
    csrf = token(page)
    assert client.get(f"/api/services/{trial_id}/edit").status_code == 404
    assert client.post(f"/api/services/{trial_id}/edit", data={
        "name": "Stolen", "category": "Other", "expires_on": "2031-01-01", "csrf_token": csrf,
    }).status_code == 404
    assert client.post(f"/api/services/{trial_id}/delete", data={"csrf_token": csrf}).status_code == 404
    with sessions() as db:
        trial = db.get(Service, trial_id)
        assert trial.name == "Private trial"
        assert str(trial.price_after_trial) == "125.50"
