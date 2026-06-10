"""真实远程视频端到端测试。

默认跳过。设置 RUN_REMOTE_VIDEO_E2E=1 后启用；YouTube 完整链路还需要配置代理或 Cookie。
"""

import os

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.user import User
from app.models.video import Video, VideoStatus
from app.services.video.url_import import (
    MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE,
    import_remote_video_from_url,
)
from app.tasks.video_download import download_video_from_url_task

RUN_REMOTE_VIDEO_E2E = os.getenv("RUN_REMOTE_VIDEO_E2E") == "1"
YOUTUBE_E2E_URL = os.getenv("REMOTE_VIDEO_E2E_YOUTUBE_URL", "https://www.youtube.com/watch?v=gSGbYOzjynk")
MOOC_E2E_URL = os.getenv(
    "REMOTE_VIDEO_E2E_MOOC_URL",
    "https://www.icourse163.org/course/PKU-1002534001?tid=1475372482",
)

pytestmark = pytest.mark.skipif(not RUN_REMOTE_VIDEO_E2E, reason="set RUN_REMOTE_VIDEO_E2E=1 to run remote E2E")


def youtube_download_configured() -> bool:
    return any(
        str(os.getenv(name) or "").strip()
        for name in (
            "YOUTUBE_DOWNLOAD_PROXY",
            "YOUTUBE_DOWNLOAD_BROWSER_COOKIE",
            "YOUTUBE_DOWNLOAD_COOKIE_FILE",
        )
    )


def create_sqlite_e2e_session(database_url: str):
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine)


def create_e2e_user(session_factory) -> User:
    db = session_factory()
    try:
        user = User(username="remote-e2e", email="remote-e2e@example.com", password="Strong#123")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def create_remote_video(session_factory, user_id: int, url: str, title: str) -> int:
    db = session_factory()
    try:
        video = Video(
            user_id=user_id,
            title=title,
            url=url,
            status=VideoStatus.DOWNLOADING,
            process_progress=0.0,
            current_step="remote e2e pending",
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        return video.id
    finally:
        db.close()


def load_video_snapshot(session_factory, video_id: int) -> dict:
    db = session_factory()
    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        return {
            "id": video.id,
            "status": video.status.value if hasattr(video.status, "value") else str(video.status),
            "progress": video.process_progress,
            "step": video.current_step,
            "error": video.error_message,
            "filepath": video.filepath,
            "subtitle_filepath": video.subtitle_filepath,
            "summary_len": len(video.summary or ""),
            "tags": video.tags,
        }
    finally:
        db.close()


def test_mooc_direct_import_is_rejected_in_gated_e2e(db):
    with pytest.raises(HTTPException) as exc_info:
        import_remote_video_from_url(
            db,
            user_id=1,
            video_url=MOOC_E2E_URL,
            process_options={"model": "tiny", "language": "zh"},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE


def test_youtube_remote_video_full_flow_when_download_configured(monkeypatch, tmp_path):
    if not youtube_download_configured():
        pytest.skip("YouTube full E2E requires proxy, browser cookie, or cookie file configuration")

    database_url = f"sqlite:///{tmp_path / 'remote_video_e2e.sqlite'}"
    upload_dir = tmp_path / "uploads"
    engine, session_factory = create_sqlite_e2e_session(database_url)
    user = create_e2e_user(session_factory)
    video_id = create_remote_video(session_factory, user.id, YOUTUBE_E2E_URL, "remote-youtube-e2e")

    monkeypatch.setattr("app.core.config.settings.DATABASE_URL", database_url)
    monkeypatch.setattr("app.core.config.settings.UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr("app.core.config.settings.SEARCH_ENABLED", False)

    try:
        download_video_from_url_task(
            video_id,
            YOUTUBE_E2E_URL,
            "youtube",
            model=os.getenv("REMOTE_VIDEO_E2E_WHISPER_MODEL", "tiny"),
            language=os.getenv("REMOTE_VIDEO_E2E_LANGUAGE", "en"),
            auto_generate_summary=True,
            auto_generate_tags=True,
            summary_style="study",
        )
        snapshot = load_video_snapshot(session_factory, video_id)
    finally:
        engine.dispose()

    assert snapshot["status"] == "completed", snapshot
    assert snapshot["progress"] == 100.0
    assert snapshot["filepath"]
    assert snapshot["subtitle_filepath"]
    assert snapshot["summary_len"] > 0
