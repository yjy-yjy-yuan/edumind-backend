"""单元测试 - 数据模型"""

import pytest
from app.models.note import Note
from app.models.note import NoteTimestamp
from app.models.subtitle import Subtitle
from app.models.user import User
from app.models.video import Video
from app.models.video import VideoStatus


@pytest.mark.unit
class TestVideoModel:
    """视频模型测试"""

    def test_video_creation(self, db):
        """测试创建视频"""
        video = Video(
            filename="test.mp4",
            filepath="/tmp/test.mp4",
            title="测试视频",
            status=VideoStatus.PENDING,
        )
        db.add(video)
        db.commit()

        assert video.id is not None
        assert video.filename == "test.mp4"
        assert video.status == VideoStatus.PENDING

    def test_video_status_enum(self, db):
        """测试视频状态枚举"""
        video = Video(
            filename="test.mp4",
            filepath="/tmp/test.mp4",
            status=VideoStatus.PROCESSING,
        )
        db.add(video)
        db.commit()

        assert video.status == VideoStatus.PROCESSING
        assert video.status.value == "processing"

    def test_video_to_dict(self, db):
        """测试视频转字典"""
        video = Video(
            filename="test.mp4",
            filepath="/tmp/test.mp4",
            title="测试",
            status=VideoStatus.COMPLETED,
        )
        db.add(video)
        db.commit()

        data = video.to_dict()
        assert "id" in data
        assert data["filename"] == "test.mp4"
        assert data["status"] == "completed"


@pytest.mark.unit
class TestUserModel:
    """用户模型测试"""

    def test_user_creation(self, db):
        """测试创建用户"""
        user = User(
            username="testuser",
            email="test@example.com",
            phone="13800138000",
            password="Strong#123",
        )
        db.add(user)
        db.commit()

        assert user.id is not None
        assert user.username == "testuser"
        assert user.phone == "13800138000"
        assert user.login_count == 0

    def test_password_hashing(self, db):
        """测试密码哈希"""
        user = User(username="testuser", email="test@example.com", password="Strong#123")
        db.add(user)
        db.commit()

        assert user.password_hash is not None
        assert user.password_hash != "Strong#123"
        assert user.password_fingerprint is not None
        assert user.check_password("Strong#123")
        assert user.has_same_password("Strong#123")
        assert not user.check_password("wrongpassword")


@pytest.mark.unit
class TestNoteModel:
    """笔记模型测试"""

    def test_note_creation(self, db, sample_video):
        """测试创建笔记"""
        note = Note(
            title="测试笔记",
            content="笔记内容",
            video_id=sample_video.id,
        )
        db.add(note)
        db.commit()

        assert note.id is not None
        assert note.title == "测试笔记"

    def test_note_timestamps(self, db, sample_video):
        """测试笔记时间戳"""
        note = Note(
            title="测试笔记",
            content="笔记内容",
            video_id=sample_video.id,
        )
        db.add(note)
        db.commit()

        timestamp = NoteTimestamp(
            note_id=note.id,
            time_seconds=60.5,
            subtitle_text="字幕文本",
        )
        db.add(timestamp)
        db.commit()

        assert timestamp.id is not None
        assert timestamp.time_seconds == 60.5


@pytest.mark.unit
class TestSubtitleModel:
    """字幕模型测试"""

    def test_subtitle_creation(self, db, sample_video):
        """测试创建字幕"""
        subtitle = Subtitle(
            video_id=sample_video.id,
            start_time=0.0,
            end_time=5.0,
            text="这是测试字幕",
            source="asr",
            language="zh",
        )
        db.add(subtitle)
        db.commit()

        assert subtitle.id is not None
        assert subtitle.text == "这是测试字幕"

    def test_subtitle_to_dict(self, db, sample_video):
        """测试字幕转字典"""
        subtitle = Subtitle(
            video_id=sample_video.id,
            start_time=0.0,
            end_time=5.0,
            text="测试",
            source="asr",
            language="zh",
        )
        db.add(subtitle)
        db.commit()

        data = subtitle.to_dict()
        assert "id" in data
        assert data["start_time"] == 0.0
        assert data["text"] == "测试"
