"""数据库连接配置。

默认使用 MySQL；当 DATABASE_URL 为 sqlite 时自动切换为 SQLite 兼容参数，
便于本地联调/验收环境使用轻量测试库。
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _build_engine():
    database_url = str(settings.DATABASE_URL or "").strip()
    url = make_url(database_url)

    if url.drivername.startswith("sqlite"):
        # SQLite 不支持 MySQL 的 charset / pool 参数；本地联调使用 check_same_thread=False
        return create_engine(
            database_url,
            echo=settings.DEBUG,
            connect_args={"check_same_thread": False},
        )

    # 默认按 MySQL 配置创建连接池
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=10,
        max_overflow=20,
        echo=settings.DEBUG,
        connect_args={"charset": "utf8mb4"},
    )


# 创建数据库引擎
engine = _build_engine()

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话 (依赖注入)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
