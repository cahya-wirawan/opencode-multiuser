import hashlib
import re
from typing import Mapping, Any

try:
    from authlib.integrations.starlette_client import OAuth
except ImportError:  # Local-only deployments can still start if OIDC deps are absent.
    OAuth = None
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import User, UserRole
from .security import role_for_new_user


oauth = OAuth() if OAuth is not None else None
_oidc_registered = False


def configure_oidc() -> None:
    global _oidc_registered
    if not settings.oidc_configured:
        return
    if oauth is None:
        raise HTTPException(status_code=503, detail="OIDC support is not installed")
    if _oidc_registered:
        return
    client_kwargs = {"scope": settings.oidc_scopes}
    if settings.oidc_use_pkce:
        client_kwargs["code_challenge_method"] = "S256"
    oauth.register(
        name="generic_oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret or None,
        server_metadata_url=settings.effective_oidc_discovery_url,
        client_kwargs=client_kwargs,
    )
    _oidc_registered = True


def oidc_client():
    if not settings.oidc_configured:
        raise HTTPException(status_code=404, detail="OIDC authentication is not configured")
    configure_oidc()
    if oauth is None:
        raise HTTPException(status_code=503, detail="OIDC support is not installed")
    client = oauth.create_client("generic_oidc")
    if client is None:
        raise HTTPException(status_code=503, detail="OIDC client is unavailable")
    return client


def _claim(userinfo: Mapping[str, Any], name: str) -> str | None:
    value = userinfo.get(name)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _username_base(userinfo: Mapping[str, Any]) -> str:
    candidates = [
        _claim(userinfo, settings.oidc_username_claim),
        _claim(userinfo, settings.oidc_email_claim),
        _claim(userinfo, settings.oidc_name_claim),
        _claim(userinfo, "sub"),
    ]
    value = next((v for v in candidates if v), "oidc-user")
    if "@" in value:
        value = value.split("@", 1)[0]
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._") or "oidc-user"
    return value[:64]


def _unique_username(db: Session, userinfo: Mapping[str, Any], issuer: str, subject: str) -> str:
    base = _username_base(userinfo)
    if db.scalar(select(User.id).where(User.username == base)) is None:
        return base
    suffix = hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()[:8]
    max_base = max(1, 80 - len(suffix) - 1)
    return f"{base[:max_base]}-{suffix}"


def provision_oidc_user(db: Session, userinfo: Mapping[str, Any]) -> User:
    subject = _claim(userinfo, "sub")
    if not subject:
        raise HTTPException(status_code=502, detail="OIDC provider did not return a subject identifier")
    issuer = settings.oidc_issuer.rstrip("/")

    user = db.scalar(
        select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject)
    )
    if user:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User account is disabled")
        user.email = _claim(userinfo, settings.oidc_email_claim)
        user.display_name = _claim(userinfo, settings.oidc_name_claim)
        db.commit()
        return user

    if not settings.oidc_auto_provision:
        raise HTTPException(status_code=403, detail="OIDC user is not provisioned for this portal")

    role = role_for_new_user(db, settings.oidc_default_role)
    user = User(
        username=_unique_username(db, userinfo, issuer, subject),
        password_hash=None,
        role=role,
        auth_provider="oidc",
        oidc_issuer=issuer,
        oidc_subject=subject,
        email=_claim(userinfo, settings.oidc_email_claim),
        display_name=_claim(userinfo, settings.oidc_name_claim),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A concurrent callback may have inserted this identity first.
        existing = db.scalar(
            select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject)
        )
        if existing:
            return existing
        raise HTTPException(status_code=409, detail="Unable to provision OIDC account") from exc
    db.refresh(user)
    return user
