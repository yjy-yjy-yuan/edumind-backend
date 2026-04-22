"""FastAPI 依赖注入"""

from typing import Generator

from app.core.database import SessionLocal
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session


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
