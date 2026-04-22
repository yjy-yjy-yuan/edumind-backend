"""认证 Pydantic Schema"""

from datetime import datetime
from typing import Optional

from app.utils.auth_security import is_strong_password
from app.utils.auth_security import is_valid_phone_number
from app.utils.auth_security import normalize_email
from app.utils.auth_security import normalize_phone_number
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator


class UserRegister(BaseModel):
    """用户注册请求"""

    username: Optional[str] = Field(None, min_length=3, max_length=64)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    password: str = Field(..., min_length=8, max_length=128)
    gender: Optional[str] = None
    education: Optional[str] = None
    occupation: Optional[str] = None
    learning_direction: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value):
        return normalize_email(value)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None

        normalized = normalize_phone_number(value)
        if not is_valid_phone_number(normalized):
            raise ValueError("请输入正确的手机号")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not is_strong_password(value):
            raise ValueError("密码至少 8 位，且必须包含大小写字母、数字和特殊字符")
        return value

    @model_validator(mode="after")
    def validate_contact(self):
        if not self.email and not self.phone:
            raise ValueError("邮箱或手机号至少填写一项")
        return self


class UserLogin(BaseModel):
    """用户登录请求"""

    account: str = Field(..., min_length=1, max_length=120)
    password: str

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("请输入邮箱或手机号")
        return normalized


class UserUpdate(BaseModel):
    """用户信息更新请求"""

    username: Optional[str] = Field(None, min_length=3, max_length=64)
    gender: Optional[str] = None
    education: Optional[str] = None
    occupation: Optional[str] = None
    learning_direction: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UserResponse(BaseModel):
    """用户响应"""

    id: int
    username: str
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    education: Optional[str] = None
    occupation: Optional[str] = None
    learning_direction: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    login_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class LoginResponse(BaseModel):
    """登录响应"""

    success: bool = True
    message: str
    user: UserResponse
    token: Optional[str] = None
