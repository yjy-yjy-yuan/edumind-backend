"""Reusable auth helpers for FastAPI routes."""

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.utils.auth_token import parse_auth_token, parse_bearer_token


def resolve_user_from_request(db: Session, user_id: Optional[int], authorization: Optional[str]) -> Optional[User]:
    """Resolve the current user.

    Default (AUTH_ALLOW_LEGACY_USER_ID_ONLY=False): only a valid Bearer token is trusted.
    Legacy mode: when the flag is True, ``user_id`` may be used only if **no** Bearer token was sent
    (Authorization 缺失或非 Bearer)。若已带 Bearer 但校验失败，**不得**回退到 ``user_id``，避免冒用。
    """
    token = parse_bearer_token(authorization)
    if token is not None:
        uid_from_token = parse_auth_token(token)
        if uid_from_token is not None:
            return db.query(User).filter(User.id == uid_from_token).first()
        return None

    if getattr(settings, "AUTH_ALLOW_LEGACY_USER_ID_ONLY", False) and user_id:
        return db.query(User).filter(User.id == user_id).first()

    return None


def resolve_user_id_from_request(
    db: Session,
    *,
    authorization: Optional[str],
    x_user_id: Optional[str] = None,
    query_user_id: Optional[str] = None,
    allow_default_user: bool = False,
    default_user_id: int = 1,
    unauthorized_detail: str = "请先登录",
) -> int:
    """Resolve user id for routes that need explicit user scoping.

    Priority:
    1. Bearer token
    2. legacy user id (X-User-ID / query user_id)
    3. optional default user fallback (development compatibility)
    """
    raw_user_id = str(x_user_id or "").strip() or str(query_user_id or "").strip() or None
    parsed_user_id: Optional[int] = None
    if raw_user_id:
        try:
            parsed_user_id = int(raw_user_id)
        except (TypeError, ValueError):
            parsed_user_id = None

    user = resolve_user_from_request(db, parsed_user_id, authorization)
    if user is not None:
        return int(user.id)

    if authorization and authorization.strip().lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=unauthorized_detail)

    if parsed_user_id is not None:
        return parsed_user_id

    if allow_default_user:
        default_email = str(getattr(settings, "DEV_DEFAULT_USER_EMAIL", "") or "").strip().lower()
        if default_email:
            default_user = db.query(User).filter(User.email == default_email).first()
            if default_user is not None:
                return int(default_user.id)
        return int(default_user_id)

    raise HTTPException(status_code=401, detail=unauthorized_detail)
