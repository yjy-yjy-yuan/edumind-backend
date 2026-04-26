"""FastAPI 依赖注入"""

from typing import Generator

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 可选: 认证依赖
# async def get_current_user(token: str = Depends(oauth2_scheme)):
#     ...
