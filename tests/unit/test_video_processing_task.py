"""视频处理任务测试"""

import json
from pathlib import Path

import pytest
from app.models.base import Base
from app.models.subtitle import Subtitle
from app.models.video import Video
from app.models.video import VideoStatus
from app.tasks.video_processing import process_video_task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.unit
def test_process_video_task_updates_video_and_subtitles(tmp_path, monkeypatch):
    """处理任务完成后应更新 videos 记录并写入 subtitles 表。"""
    from app.core.config import settings

    db_path = tmp_path / "video-processing.sqlite3"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    video_path = tmp_path / "lesson.mp4"
    video_path.write_bytes(b"fake-video")

    session = SessionLocal()
    video = Video(
        filename="lesson.mp4",
        filepath=str(video_path),
        title="lesson",
        status=VideoStatus.UPLOADED,
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    video_id = video.id
    session.close()

    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    monkeypatch.setattr(settings, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(settings, "WHISPER_MODEL_PATH", str(tmp_path / "whisper"))

    def fake_generate_preview_image(_video_path, preview_path):
        preview = Path(preview_path)
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"preview")
        return True

    def fake_generate_video_info(_video_path):
        return {"width": 1280, "height": 720, "fps": 30.0, "frame_count": 300, "duration": 10.0}

    def fake_extract_audio(_video_path, _audio_path):
        return False

    def fake_transcribe(_input_file, _model_name, _language, _model_path):
        return {
            "text": "第一句 第二句",
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "第一句"},
                {"start": 1.2, "end": 2.8, "text": "第二句"},
            ],
        }

    def fake_save_subtitles(_result, srt_path, txt_path):
        srt = Path(srt_path)
        txt = Path(txt_path)
        srt.parent.mkdir(parents=True, exist_ok=True)
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,200\n第一句\n\n2\n00:00:01,200 --> 00:00:02,800\n第二句\n", encoding="utf-8"
        )
        txt.write_text("第一句\n第二句\n", encoding="utf-8")
        return True

    def fake_generate_summary(_video_id, _subtitle_path="", **_kwargs):
        return {
            "success": True,
            "summary": "主题：牛顿第二定律与受力分析\n学习重点：\n1. 受力分析步骤。\n2. 合力与加速度关系。",
        }

    def fake_generate_tags(_video_id, _summary, **_kwargs):
        return {"success": True, "tags": ["牛顿第二定律", "受力分析", "合力"]}

    monkeypatch.setattr("app.tasks.video_processing.generate_preview_image", fake_generate_preview_image)
    monkeypatch.setattr("app.tasks.video_processing.generate_video_info", fake_generate_video_info)
    monkeypatch.setattr("app.tasks.video_processing.extract_audio", fake_extract_audio)
    monkeypatch.setattr("app.tasks.video_processing.transcribe_with_whisper", fake_transcribe)
    monkeypatch.setattr("app.tasks.video_processing.save_subtitles", fake_save_subtitles)
    monkeypatch.setattr("app.services.video_content_service.generate_video_summary", fake_generate_summary)
    monkeypatch.setattr("app.services.video_content_service.generate_video_tags", fake_generate_tags)

    result = process_video_task(
        video_id,
        "zh",
        "turbo",
        auto_generate_summary=True,
        auto_generate_tags=True,
        summary_style="study",
    )

    assert result["status"] == "success"

    verify_session = SessionLocal()
    stored_video = verify_session.query(Video).filter(Video.id == video_id).first()
    stored_subtitles = (
        verify_session.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.start_time.asc()).all()
    )

    assert stored_video is not None
    assert stored_video.status == VideoStatus.COMPLETED
    assert stored_video.current_step == "处理完成（turbo）"
    assert stored_video.process_progress == 100.0
    assert stored_video.subtitle_filepath is not None
    assert stored_video.summary == "主题：牛顿第二定律与受力分析\n学习重点：\n1. 受力分析步骤。\n2. 合力与加速度关系。"
    assert json.loads(stored_video.tags) == ["牛顿第二定律", "受力分析", "合力"]
    assert stored_video.title == "牛顿第二定律与受力分析"
    assert stored_video.filename == "牛顿第二定律与受力分析.mp4"
    assert stored_video.filepath == str(tmp_path / "牛顿第二定律与受力分析.mp4")
    assert Path(stored_video.filepath).exists()
    assert not video_path.exists()
    assert Path(stored_video.subtitle_filepath).exists()
    assert len(stored_subtitles) == 2
    assert stored_subtitles[0].text == "第一句"
    assert stored_subtitles[1].text == "第二句"

    verify_session.close()
    engine.dispose()
