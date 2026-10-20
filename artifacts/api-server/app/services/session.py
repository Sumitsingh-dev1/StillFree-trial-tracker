import secrets

from fastapi import Form, HTTPException, Request


def csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return request.session["csrf_token"]


def validate_csrf(request: Request, csrf_token: str = Form("")) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(status_code=403, detail="Form expired. Reload the page and try again.")
