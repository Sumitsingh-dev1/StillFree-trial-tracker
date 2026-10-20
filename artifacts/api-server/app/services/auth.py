import bcrypt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User


class EmailAlreadyRegistered(Exception):
    pass


def register_user(db: Session, email: str, password: str) -> User:
    password_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise EmailAlreadyRegistered from error
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        return None

    if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        return None
    return user
