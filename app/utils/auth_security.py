"""认证安全辅助函数。"""

import hashlib
import hmac
import re
from typing import Optional

from app.core.config import settings

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^(?:\+?86)?1[3-9]\d{9}$")
STRONG_PASSWORD_PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9])[^\s]{8,128}$")


def normalize_email(value: Optional[str]) -> Optional[str]:
    """标准化邮箱。"""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def looks_like_email(value: str) -> bool:
    """判断是否为邮箱。"""
    return bool(EMAIL_PATTERN.fullmatch(str(value or "").strip().lower()))


def normalize_phone_number(value: Optional[str]) -> Optional[str]:
    """标准化手机号，统一为 11 位中国大陆手机号。"""
    if value is None:
        return None

    raw = str(value).strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits or None


def is_valid_phone_number(value: Optional[str]) -> bool:
    """判断手机号格式是否合法。"""
    if value is None:
        return False

    normalized = normalize_phone_number(value)
    return bool(normalized and PHONE_PATTERN.fullmatch(normalized))


def is_strong_password(value: str) -> bool:
    """判断密码是否满足强密码要求。"""
    return bool(STRONG_PASSWORD_PATTERN.fullmatch(str(value or "")))


def build_password_fingerprint(password: str) -> str:
    """生成用于重复密码检测的稳定指纹。"""
    secret = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(secret, password.encode("utf-8"), hashlib.sha256).hexdigest()
