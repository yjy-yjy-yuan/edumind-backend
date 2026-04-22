"""pytest 配置文件 - FastAPI 测试"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.base import Base

# 使用内存数据库进行测试
SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """覆盖数据库依赖"""
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


@pytest.fixture(scope="function")
def db():
    """数据库会话 fixture"""
    # 创建所有表
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        # 清理所有表
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    """测试客户端 fixture"""
    original_preload = settings.WHISPER_PRELOAD_ON_STARTUP
    settings.WHISPER_PRELOAD_ON_STARTUP = False

    try:
        # 覆盖数据库依赖
        app.dependency_overrides[get_db] = override_get_db

        # 创建所有表
        Base.metadata.create_all(bind=engine)

        with TestClient(app) as c:
            yield c
    finally:
        settings.WHISPER_PRELOAD_ON_STARTUP = original_preload
        Base.metadata.drop_all(bind=engine)
        app.dependency_overrides.clear()


@pytest.fixture
def sample_video(db, sample_user):
    """示例视频 fixture"""
    from app.models.video import Video
    from app.models.video import VideoStatus

    video = Video(
        user_id=sample_user.id,
        filename="test_video.mp4",
        filepath="/tmp/test_video.mp4",
        title="测试视频",
        status=VideoStatus.PENDING,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


@pytest.fixture
def sample_note(db, sample_video):
    """示例笔记 fixture"""
    from app.models.note import Note

    note = Note(
        title="测试笔记",
        content="这是测试笔记的内容",
        video_id=sample_video.id,
        note_type="text",
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@pytest.fixture
def sample_user(db):
    """示例用户 fixture"""
    from app.models.user import User

    user = User(
        username="testuser",
        email="test@example.com",
        phone="13800138000",
        password="Strong#123",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
