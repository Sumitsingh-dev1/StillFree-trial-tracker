from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.auth import Credentials
from app.services.auth import (
    EmailAlreadyRegistered,
    authenticate_user,
    register_user,
)
from app.services.session import validate_csrf
from app.views import templates

router = APIRouter(prefix="/api")


def _validation_message(error: ValidationError) -> str:
    return str(error.errors()[0]["msg"])


@router.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={"error": None, "email": ""},
    )


@router.post("/signup", dependencies=[Depends(validate_csrf)])
def signup(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    db: Session = Depends(get_db),
):
    try:
        credentials = Credentials(email=email, password=password)
    except ValidationError as error:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": _validation_message(error),
                "email": email.strip().lower(),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = register_user(db, credentials.email, credentials.password)
    except EmailAlreadyRegistered:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": "An account with this email already exists.",
                "email": credentials.email,
            },
            status_code=status.HTTP_409_CONFLICT,
        )

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/api/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "email": ""},
    )


@router.post("/login", dependencies=[Depends(validate_csrf)])
def login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    db: Session = Depends(get_db),
):
    try:
        credentials = Credentials(email=email, password=password)
    except ValidationError as error:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": _validation_message(error),
                "email": email.strip().lower(),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate_user(db, credentials.email, credentials.password)
    if user is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Email or password is incorrect.",
                "email": credentials.email,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/api/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout", dependencies=[Depends(validate_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/api/login", status_code=status.HTTP_303_SEE_OTHER)
