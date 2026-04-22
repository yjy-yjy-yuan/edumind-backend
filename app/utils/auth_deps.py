"""Reusable auth helpers for FastAPI routes."""

from typing import Optional

from app.core.config import settings
from app.models.user import User
from app.utils.auth_token import parse_auth_token
from app.utils.auth_token import parse_bearer_token
from sqlalchemy.orm import Session


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
