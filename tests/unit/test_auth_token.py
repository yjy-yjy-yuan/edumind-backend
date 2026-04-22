"""认证 token 单元测试。"""

import hashlib
import hmac

import pytest
from app.core.config import settings
from app.utils import auth_token


@pytest.mark.unit
def test_build_and_parse_auth_token_with_expiry(monkeypatch):
    """新 token 应包含过期时间并可被正常解析。"""
    monkeypatch.setattr(settings, "AUTH_TOKEN_TTL_SECONDS", 120)
    monkeypatch.setattr(settings, "AUTH_TOKEN_CLOCK_SKEW_SECONDS", 0)
    monkeypatch.setattr(auth_token.time, "time", lambda: 1_700_000_000)

    token = auth_token.build_auth_token(42)
    parsed_user_id = auth_token.parse_auth_token(token)

    assert token.startswith("42.1700000120.")
    assert parsed_user_id == 42


@pytest.mark.unit
def test_parse_auth_token_rejects_expired_token(monkeypatch):
    """超过过期时间的新 token 应返回 None。"""
    monkeypatch.setattr(settings, "AUTH_TOKEN_CLOCK_SKEW_SECONDS", 0)
    monkeypatch.setattr(auth_token.time, "time", lambda: 1_700_000_100)

    payload = "7.1700000000"
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}.{signature}"

    assert auth_token.parse_auth_token(token) is None


@pytest.mark.unit
def test_parse_auth_token_is_backward_compatible(monkeypatch):
    """旧 token 格式 userId.signature 仍可解析。"""
    monkeypatch.setattr(auth_token.time, "time", lambda: 1_700_000_000)

    payload = "19"
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}.{signature}"

    assert auth_token.parse_auth_token(token) == 19
