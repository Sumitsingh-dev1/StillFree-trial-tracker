from pathlib import Path
from decimal import Decimal

from fastapi.templating import Jinja2Templates

from app.services.session import csrf_token

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["csrf_token"] = csrf_token


def inr(value) -> str:
    """Format INR using Indian digit grouping without losing decimal precision."""
    amount = f"{Decimal(value):.2f}"
    whole, fraction = amount.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([*groups, tail])
    return f"₹{whole}.{fraction}"


templates.env.filters["inr"] = inr
