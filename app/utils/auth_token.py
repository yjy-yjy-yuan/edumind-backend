"""轻量认证 token 工具。"""

import hashlib
import hmac
import time
from typing import Optional

from app.core.config import settings


def build_auth_token(user_id: int) -> str:
    """为当前用户生成签名 token。"""
    expires_at = int(time.time()) + max(1, int(settings.AUTH_TOKEN_TTL_SECONDS))
    payload = f"{int(user_id)}.{expires_at}"
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def parse_auth_token(token: Optional[str]) -> Optional[int]:
    """校验 token 并提取用户 ID。"""
    if not token:
        return None

    parts = token.split(".")
    if len(parts) == 2:
        payload, signature = parts
        expected = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if not payload.isdigit():
            return None
        return int(payload)

    if len(parts) != 3:
        return None

    user_id_raw, expires_at_raw, signature = parts
    if not user_id_raw.isdigit() or not expires_at_raw.isdigit():
        return None

    payload = f"{user_id_raw}.{expires_at_raw}"
    expected = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    now = int(time.time())
    expires_at = int(expires_at_raw)
    skew = max(0, int(settings.AUTH_TOKEN_CLOCK_SKEW_SECONDS))
    if expires_at + skew < now:
        return None

    return int(user_id_raw)


def parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization 头中提取 Bearer token。"""
    if not authorization:
        return None

    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()
