"""A valid signed cookie cannot authenticate an account that no longer exists."""

from http.cookies import SimpleCookie

from sqlalchemy import delete, select

from app.models import Service, User


def test_deleted_account_cookie_redirects_private_pages_to_login(
    client, register, token, sessions,
):
    # The shared fixtures disable reminders, block real email, and use SQLite.
    assert not hasattr(client.app.state, "reminder_scheduler")
    assert register(client).status_code == 303
    created = client.post("/api/services", data={
        "name": "Deleted account private trial",
        "category": "Cloud",
        "expires_on": "2030-01-01",
        "price_after_trial": "125.50",
        "cancel_url": "",
        "csrf_token": token(client.get("/api/services/new")),
    })
    assert created.status_code == 303
    with sessions() as db:
        user_id = db.scalar(select(User.id))
        trial_id = db.scalar(select(Service.id))
    assert user_id is not None
    assert trial_id is not None

    paths = (
        "/api/dashboard",
        "/api/services/new",
        f"/api/services/{trial_id}/edit",
    )
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200
        if path != "/api/services/new":
            assert "Deleted account private trial" in page.text
    old_cookie = client.cookies.get("stillfree_session")
    assert old_cookie

    with sessions() as db:
        # Respect the real non-cascading foreign key before deleting the owner.
        # The previously accessible edit URL must redirect, not become a 404.
        db.execute(delete(Service).where(Service.user_id == user_id))
        db.delete(db.get(User, user_id))
        db.commit()
        assert db.get(User, user_id) is None

    for path in paths:
        # Replay the unchanged signed cookie on every request, even after the
        # previous response has instructed the browser to discard its session.
        client.cookies.clear()
        response = client.get(path, headers={
            "Cookie": f"stillfree_session={old_cookie}",
        })
        assert response.request.headers["cookie"] == (
            f"stillfree_session={old_cookie}"
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/api/login"
        assert "Deleted account private trial" not in response.text
        expired = SimpleCookie()
        expired.load(response.headers["set-cookie"])
        assert expired["stillfree_session"]["expires"] == (
            "Thu, 01 Jan 1970 00:00:00 GMT"
        )
