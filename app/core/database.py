"""数据库连接配置 - SQLAlchemy 2.0 (MySQL)"""

from typing import Generator

from app.core.config import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

# 创建数据库引擎 (MySQL)
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=10,
    max_overflow=20,
    echo=settings.DEBUG,
    connect_args={"charset": "utf8mb4"},
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话 (依赖注入)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
