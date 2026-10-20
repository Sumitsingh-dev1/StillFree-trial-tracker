from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.routers.trials import current_user
from app.schemas.service import CATEGORIES
from app.services.trials import dashboard_data, today_date
from app.views import templates

router = APIRouter(prefix="/api")


@router.get("/")
def home(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/api/dashboard", status_code=303)
    return RedirectResponse("/api/login", status_code=303)


@router.get("/dashboard")
def dashboard(
    request: Request,
    category: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if category and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category.")
    today = today_date()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "categories": CATEGORIES,
            "selected_category": category,
            "today": today,
            **dashboard_data(db, user.id, category, today),
        },
    )
