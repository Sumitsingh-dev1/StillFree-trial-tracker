"""Invalid signed sessions must not expose authenticated pages."""

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.main import app
from app.models import Service


@pytest.mark.parametrize("cookie_state", ["tampered", "expired"])
def test_invalid_session_redirects_private_pages_to_login(
    client, register, token, sessions, monkeypatch, cookie_state,
):
    # Freeze only the signing clock: no sleeping or browser-cookie expiration.
    issued_at = 2_000_000_000
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: issued_at)
    middleware = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    cookie_name = middleware.kwargs["session_cookie"]
    max_age = middleware.kwargs["max_age"]

    assert register(client).status_code == 303
    response = client.post("/api/services", data={
        "name": "Confidential trial",
        "category": "Cloud",
        "expires_on": "2030-01-01",
        "price_after_trial": "125.50",
        "cancel_url": "",
        "csrf_token": token(client.get("/api/services/new")),
    })
    assert response.status_code == 303
    with sessions() as db:
        trial_id = db.scalar(select(Service.id))
    assert trial_id is not None

    paths = (
        "/api/dashboard",
        "/api/services/new",
        f"/api/services/{trial_id}/edit",
    )
    # Prove the cookie authenticates an existing owner and an existing trial.
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200
        if path != "/api/services/new":
            assert "Confidential trial" in page.text
    cookie = client.cookies.get(cookie_name)
    assert cookie

    if cookie_state == "tampered":
        payload, timestamp, signature = cookie.rsplit(".", 2)
        changed = "A" if signature[0] != "A" else "B"
        cookie = f"{payload}.{timestamp}.{changed}{signature[1:]}"
    else:
        # Keep the original valid signature, but exceed the app's actual TTL.
        monkeypatch.setattr(
            TimestampSigner, "get_timestamp", lambda self: issued_at + max_age + 1,
        )

    for path in paths:
        client.cookies.clear()
        response = client.get(path, headers={"Cookie": f"{cookie_name}={cookie}"})
        assert response.request.headers["cookie"] == f"{cookie_name}={cookie}"
        assert response.status_code == 303
        assert response.headers["location"] == "/api/login"
        assert "Confidential trial" not in response.text
