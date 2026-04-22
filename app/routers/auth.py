"""用户认证路由 - FastAPI 版本"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import UserLogin
from app.schemas.auth import UserRegister
from app.schemas.auth import UserUpdate
from app.utils.auth_security import build_password_fingerprint
from app.utils.auth_security import is_valid_phone_number
from app.utils.auth_security import looks_like_email
from app.utils.auth_security import normalize_email
from app.utils.auth_security import normalize_phone_number
from app.utils.auth_token import build_auth_token
from app.utils.auth_token import parse_auth_token
from app.utils.auth_token import parse_bearer_token
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Header
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()

AVATAR_ROUTE_PREFIX = "/api/auth/avatar-files/"
AVATAR_SUBDIRECTORY = "avatars"
MAX_AVATAR_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
ALLOWED_AVATAR_CONTENT_TYPES = {
    "image/gif",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def build_default_username(email: Optional[str], phone: Optional[str]) -> str:
    """根据登录标识生成默认用户名。"""
    if email:
        local_part = email.split("@", 1)[0]
        sanitized = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in local_part).strip("_")
        return (sanitized or "user")[:64]
    if phone:
        return f"user_{phone[-4:]}"
    return "user"


def normalize_preferred_username(preferred: Optional[str]) -> Optional[str]:
    """标准化用户显式输入的用户名。"""
    if preferred is None:
        return None
    normalized = preferred.strip()
    return normalized or None


def ensure_unique_username(
    db: Session,
    preferred: Optional[str],
    email: Optional[str],
    phone: Optional[str],
    exclude_user_id: Optional[int] = None,
) -> str:
    """生成唯一用户名。"""
    normalized_preferred = normalize_preferred_username(preferred)
    if normalized_preferred:
        existing_query = db.query(User.id).filter(User.username == normalized_preferred)
        if exclude_user_id is not None:
            existing_query = existing_query.filter(User.id != exclude_user_id)
        if existing_query.first():
            raise HTTPException(status_code=400, detail="用户名已存在")
        return normalized_preferred

    base = build_default_username(email, phone)
    base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in base).strip("_") or "user"
    base = base[:64]
    candidate = base
    suffix = 1

    while db.query(User.id).filter(User.username == candidate).first():
        suffix_text = f"_{suffix}"
        candidate = f"{base[: max(1, 64 - len(suffix_text))]}{suffix_text}"
        suffix += 1

    return candidate


def find_user_by_account(db: Session, account: str) -> Optional[User]:
    """按邮箱或手机号查找用户。"""
    normalized_account = account.strip()
    if looks_like_email(normalized_account):
        return db.query(User).filter(User.email == normalize_email(normalized_account)).first()

    normalized_phone = normalize_phone_number(normalized_account)
    if normalized_phone and is_valid_phone_number(normalized_phone):
        return db.query(User).filter(User.phone == normalized_phone).first()

    return None


def resolve_current_user_id(user_id: Optional[int], authorization: Optional[str]) -> Optional[int]:
    """优先从 Bearer token 中解析用户 ID，保留 query 参数兼容旧链路。"""
    token = parse_bearer_token(authorization)
    return parse_auth_token(token) or user_id


def get_avatar_upload_dir() -> Path:
    """头像上传目录。"""
    return Path(settings.UPLOAD_FOLDER) / AVATAR_SUBDIRECTORY


def build_avatar_url(filename: str) -> str:
    """头像文件对应的可访问路径。"""
    return f"{AVATAR_ROUTE_PREFIX}{filename}"


def resolve_managed_avatar_file(avatar_value: Optional[str]) -> Optional[Path]:
    """将数据库中的头像访问路径映射回受控文件路径。"""
    if not avatar_value or not avatar_value.startswith(AVATAR_ROUTE_PREFIX):
        return None

    filename = Path(avatar_value.removeprefix(AVATAR_ROUTE_PREFIX)).name
    if not filename:
        return None

    return get_avatar_upload_dir() / filename


def delete_managed_avatar_file(avatar_value: Optional[str]) -> None:
    """删除旧头像文件，仅处理当前后端管理的头像目录。"""
    file_path = resolve_managed_avatar_file(avatar_value)
    if file_path is None or not file_path.exists():
        return

    try:
        file_path.unlink()
    except OSError as exc:
        logger.warning("删除旧头像文件失败 | path=%s | error=%s", file_path, exc)


def get_current_user_or_404(db: Session, user_id: Optional[int], authorization: Optional[str]) -> User:
    """解析当前登录用户。"""
    current_user_id = resolve_current_user_id(user_id, authorization)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="未登录")

    user = db.query(User).filter(User.id == current_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


async def save_avatar_file(file: UploadFile, user_id: int) -> str:
    """保存头像文件并返回数据库中存储的访问路径。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_AVATAR_EXTENSIONS:
        raise HTTPException(status_code=400, detail="头像仅支持 jpg、jpeg、png、gif、webp、heic、heif")

    content_type = (file.content_type or "").lower()
    if content_type and content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="头像文件类型不正确")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="头像文件不能为空")
    if len(content) > MAX_AVATAR_FILE_SIZE:
        raise HTTPException(status_code=400, detail="头像大小不能超过 5MB")

    avatar_dir = get_avatar_upload_dir()
    avatar_dir.mkdir(parents=True, exist_ok=True)

    filename = f"user_{user_id}_{uuid4().hex}{suffix}"
    (avatar_dir / filename).write_bytes(content)
    return build_avatar_url(filename)


@router.post("/register", response_model=dict)
async def register(data: UserRegister, db: Session = Depends(get_db)):
    """用户注册"""
    email = normalize_email(data.email)
    phone = normalize_phone_number(data.phone)
    password_fingerprint = build_password_fingerprint(data.password)

    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="邮箱已存在")
    if phone and db.query(User).filter(User.phone == phone).first():
        raise HTTPException(status_code=400, detail="手机号已存在")
    if db.query(User).filter(User.password_fingerprint == password_fingerprint).first():
        raise HTTPException(status_code=400, detail="该密码已被使用，请更换一个不重复的强密码")

    username = ensure_unique_username(db, data.username, email, phone)

    try:
        user = User(
            username=username,
            email=email,
            phone=phone,
            password=data.password,
            gender=data.gender,
            education=data.education,
            occupation=data.occupation,
            learning_direction=data.learning_direction,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        return {"success": True, "message": "注册成功", "user": user.to_dict()}
    except Exception as e:
        db.rollback()
        logger.error(f"注册失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"注册失败: {str(e)}")


@router.post("/login", response_model=dict)
async def login(data: UserLogin, db: Session = Depends(get_db)):
    """用户登录"""
    user = find_user_by_account(db, data.account)
    if user is None:
        raise HTTPException(status_code=401, detail="邮箱/手机号或密码错误")

    if user.check_password(data.password):
        # 更新最后登录时间
        user.last_login = datetime.utcnow()
        user.login_count += 1
        if not user.password_fingerprint:
            user.password_fingerprint = build_password_fingerprint(data.password)
        db.commit()

        return {
            "success": True,
            "message": "登录成功",
            "user": user.to_dict(),
            "token": build_auth_token(user.id),
        }

    raise HTTPException(status_code=401, detail="邮箱/手机号或密码错误")


@router.post("/logout", response_model=dict)
async def logout():
    """用户退出登录"""
    return {"success": True, "message": "退出成功"}


@router.get("/user", response_model=dict)
async def get_user(
    user_id: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """获取当前登录用户信息"""
    user = get_current_user_or_404(db, user_id, authorization)
    return {"success": True, "user": user.to_dict(), "token": build_auth_token(user.id)}


@router.post("/user/update", response_model=dict)
async def update_user(
    data: UserUpdate,
    user_id: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """更新用户信息"""
    user = get_current_user_or_404(db, user_id, authorization)

    if "username" in data.model_fields_set and data.username is None:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    # 更新字段
    if data.username is not None:
        user.username = ensure_unique_username(
            db,
            data.username,
            user.email,
            user.phone,
            exclude_user_id=user.id,
        )
    if data.gender is not None:
        user.gender = data.gender
    if data.education is not None:
        user.education = data.education
    if data.occupation is not None:
        user.occupation = data.occupation
    if data.learning_direction is not None:
        user.learning_direction = data.learning_direction
    if data.bio is not None:
        user.bio = data.bio
    if data.avatar is not None:
        user.avatar = data.avatar

    try:
        db.commit()
        db.refresh(user)
        return {"success": True, "message": "更新成功", "user": user.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@router.post("/user/avatar", response_model=dict)
async def upload_user_avatar(
    file: UploadFile = File(...),
    user_id: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """上传用户头像。"""
    user = get_current_user_or_404(db, user_id, authorization)
    previous_avatar = user.avatar
    avatar_url = await save_avatar_file(file, user.id)
    user.avatar = avatar_url

    try:
        db.commit()
        db.refresh(user)
    except Exception as exc:
        db.rollback()
        delete_managed_avatar_file(avatar_url)
        raise HTTPException(status_code=500, detail=f"头像上传失败: {str(exc)}")

    if previous_avatar != avatar_url:
        delete_managed_avatar_file(previous_avatar)

    return {"success": True, "message": "头像上传成功", "user": user.to_dict()}


@router.get("/avatar-files/{filename}")
async def get_avatar_file(filename: str):
    """读取用户头像文件。"""
    safe_filename = Path(filename).name
    file_path = get_avatar_upload_dir() / safe_filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在")

    return FileResponse(path=str(file_path))
