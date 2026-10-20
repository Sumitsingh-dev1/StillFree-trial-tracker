from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Service, User
from app.schemas.service import CATEGORIES, ServiceInput
from app.services.session import validate_csrf
from app.services.trials import apply_input
from app.views import templates

router = APIRouter(prefix="/api/services")
FIELDS = ("name", "category", "expires_on", "price_after_trial", "cancel_url")


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        request.session.clear()
        raise LoginRequired
    return user


def owned_service(db: Session, service_id: int, user_id: int) -> Service:
    # Resolve by both IDs: another user's records always look nonexistent.
    service = db.scalar(
        select(Service).where(Service.id == service_id, Service.user_id == user_id)
    )
    if service is None:
        raise HTTPException(status_code=404, detail="Trial not found.")
    return service


def form_page(request, user, values, service_id=None, errors=None):
    return templates.TemplateResponse(
        request=request,
        name="service_form.html",
        context={
            "user": user,
            "categories": CATEGORIES,
            "values": values,
            "errors": errors or {},
            "is_edit": service_id is not None,
            "service_id": service_id,
        },
        status_code=422 if errors else 200,
    )


@router.get("/new")
def new_trial(request: Request, user: User = Depends(current_user)):
    return form_page(request, user, dict.fromkeys(FIELDS, ""))


@router.get("/{service_id}/edit")
def edit_trial(
    service_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    trial = owned_service(db, service_id, user.id)
    values = {field: str(getattr(trial, field)) if getattr(trial, field) is not None else "" for field in FIELDS}
    return form_page(request, user, values, service_id=trial.id)


def save_trial(request, user, db, values, trial=None):
    try:
        parsed = ServiceInput(**values)
    except ValidationError as error:
        errors = {}
        for item in error.errors():
            errors.setdefault(str(item["loc"][0]), item["msg"].removeprefix("Value error, "))
        return form_page(
            request, user, values, service_id=trial.id if trial else None, errors=errors
        )
    if trial is None:
        trial = Service(user_id=user.id)
        db.add(trial)
    apply_input(trial, parsed)
    db.commit()
    return RedirectResponse("/api/dashboard", status_code=303)


@router.post("", dependencies=[Depends(validate_csrf)])
def create_trial(
    request: Request,
    name: str = Form(""),
    category: str = Form(""),
    expires_on: str = Form(""),
    price_after_trial: str = Form(""),
    cancel_url: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    values = dict(zip(FIELDS, (name, category, expires_on, price_after_trial, cancel_url)))
    return save_trial(request, user, db, values)


@router.post("/{service_id}/edit", dependencies=[Depends(validate_csrf)])
def update_trial(
    service_id: int,
    request: Request,
    name: str = Form(""),
    category: str = Form(""),
    expires_on: str = Form(""),
    price_after_trial: str = Form(""),
    cancel_url: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    trial = owned_service(db, service_id, user.id)
    values = dict(zip(FIELDS, (name, category, expires_on, price_after_trial, cancel_url)))
    return save_trial(request, user, db, values, trial)


@router.post("/{service_id}/delete", dependencies=[Depends(validate_csrf)])
def delete_trial(
    service_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    trial = owned_service(db, service_id, user.id)
    db.delete(trial)
    db.commit()
    return RedirectResponse("/api/dashboard", status_code=303)
