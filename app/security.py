from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import User, UserRole

ph = PasswordHasher()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def role_for_new_user(db: Session, default_role: str = UserRole.DEVELOPER.value) -> str:
    """Return ADMIN for the first provisioned account, otherwise default_role.

    PostgreSQL takes a short table lock so two simultaneous first registrations
    cannot both become administrators.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    count = db.scalar(select(func.count(User.id))) or 0
    if count == 0:
        return UserRole.ADMIN.value
    return default_role if default_role in {r.value for r in UserRole} else UserRole.DEVELOPER.value


def issue_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "usr": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def load_user_from_token(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id = payload["sub"]
    except Exception:
        return None
    return db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))


def current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = token or request.cookies.get(settings.session_cookie_name)
    user = load_user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing authentication")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user
