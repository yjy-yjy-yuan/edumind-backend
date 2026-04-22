"""API 测试 - 视频接口"""

import io
import json
import os

import pytest


def fake_upload_recommendations(*args, **kwargs):
    """上传后自动返回的推荐结果假数据。"""
    return {
        "scene": "related",
        "strategy": "video_status_interest_v1",
        "items": [
            {
                "id": 88,
                "title": "站内导数复盘",
                "is_external": False,
                "source_label": "站内视频",
                "summary": "围绕导数定义与函数单调性做复盘。",
                "tags": ["数学", "导数"],
                "reason_text": "与当前视频共享导数主题。",
            }
        ],
        "external_item_count": 0,
        "internal_item_count": 1,
    }


@pytest.mark.api
class TestVideoAPI:
    """视频 API 测试"""

    def test_get_videos_empty(self, client):
        """测试获取空视频列表"""
        response = client.get("/api/videos/list")
        assert response.status_code == 200
        data = response.json()
        assert data["videos"] == []

    def test_get_videos_with_data(self, client, sample_video):
        """测试获取视频列表"""
        response = client.get("/api/videos/list")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["filename"] == "test_video.mp4"

    def test_get_video_by_id(self, client, sample_video):
        """测试通过 ID 获取视频"""
        response = client.get(f"/api/videos/{sample_video.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_video.id

    def test_get_video_not_found(self, client):
        """测试获取不存在的视频"""
        response = client.get("/api/videos/99999")
        assert response.status_code == 404

    def test_generate_tags_includes_subject_tag(self, client, db, sample_user, monkeypatch):
        """重提标签后应写回科目标签，便于视频详情页直接展示。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        monkeypatch.setattr("app.services.video_content_service.call_online_chat", lambda *args, **kwargs: None)
        monkeypatch.setattr("app.services.video_content_service.call_ollama", lambda *args, **kwargs: None)

        video = Video(
            user_id=sample_user.id,
            title="勾股定理详细讲解",
            filename="math.mp4",
            filepath="/tmp/math.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="讲解勾股定理、直角三角形性质和几何证明方法。",
            tags=None,
        )
        db.add(video)
        db.commit()
        db.refresh(video)

        response = client.post(f"/api/videos/{video.id}/generate-tags", json={"max_tags": 6})
        assert response.status_code == 200

        payload = response.json()
        assert payload["success"] is True
        assert payload["tags"][0] == "数学"
        assert any("勾股" in tag for tag in payload["tags"])

        db.refresh(video)
        stored_tags = json.loads(video.tags)
        assert stored_tags[0] == "数学"

    def test_delete_video(self, client, sample_video):
        """测试删除视频"""
        response = client.delete(f"/api/videos/{sample_video.id}/delete")
        assert response.status_code == 200

        # 验证删除
        response = client.get(f"/api/videos/{sample_video.id}")
        assert response.status_code == 404

    def test_delete_video_cascades_subtitles_notes_and_timestamps(self, client, db, sample_user):
        """删除视频前先清理字幕、笔记时间戳与笔记，避免外键失败。"""
        from app.models.note import Note
        from app.models.note import NoteTimestamp
        from app.models.subtitle import Subtitle
        from app.models.video import Video
        from app.models.video import VideoStatus

        video = Video(
            user_id=sample_user.id,
            filename="cascade.mp4",
            filepath="/tmp/cascade.mp4",
            title="级联删除测",
            status=VideoStatus.COMPLETED,
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        db.add(
            Subtitle(
                video_id=video.id,
                start_time=0.0,
                end_time=1.0,
                text="subtitle",
                source="asr",
                language="zh",
            )
        )
        note = Note(title="笔记", content="内容", video_id=video.id, note_type="text")
        db.add(note)
        db.commit()
        db.refresh(note)
        db.add(NoteTimestamp(note_id=note.id, time_seconds=1.0, subtitle_text="戳"))
        db.commit()

        note_id = note.id
        video_id = video.id

        response = client.delete(f"/api/videos/{video_id}/delete")
        assert response.status_code == 200

        assert db.query(Subtitle).filter(Subtitle.video_id == video_id).count() == 0
        assert db.query(Note).filter(Note.video_id == video_id).count() == 0
        assert db.query(NoteTimestamp).filter(NoteTimestamp.note_id == note_id).count() == 0
        assert db.query(Video).filter(Video.id == video_id).count() == 0

    def test_get_video_status(self, client, sample_video):
        """测试获取视频处理状态"""
        response = client.get(f"/api/videos/{sample_video.id}/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "progress" in data
        assert "requested_model" in data

    def test_stream_video_file_supports_full_content(self, client, db, sample_video, tmp_path):
        """测试视频全量流接口"""
        content = b"0123456789abcdef"
        video_path = tmp_path / "stream-full.mp4"
        video_path.write_bytes(content)

        sample_video.filepath = str(video_path)
        sample_video.filename = "stream-full.mp4"
        db.commit()

        response = client.get(f"/api/videos/{sample_video.id}/stream")
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == str(len(content))

    def test_stream_video_file_supports_range_requests(self, client, db, sample_video, tmp_path):
        """测试视频流接口支持 Range，便于 iOS 播放器拖动和断点加载"""
        content = b"0123456789abcdef"
        video_path = tmp_path / "stream-range.mp4"
        video_path.write_bytes(content)

        sample_video.filepath = str(video_path)
        sample_video.filename = "stream-range.mp4"
        db.commit()

        response = client.get(
            f"/api/videos/{sample_video.id}/stream",
            headers={"Range": "bytes=2-7"},
        )
        assert response.status_code == 206
        assert response.content == content[2:8]
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-range"] == f"bytes 2-7/{len(content)}"
        assert response.headers["content-length"] == "6"

    def test_stream_video_file_supports_head_requests(self, client, db, sample_video, tmp_path):
        """测试视频流接口支持 HEAD，兼容 iOS/WKWebView 预检。"""
        content = b"0123456789abcdef"
        video_path = tmp_path / "stream-head.mp4"
        video_path.write_bytes(content)

        sample_video.filepath = str(video_path)
        sample_video.filename = "stream-head.mp4"
        db.commit()

        response = client.head(f"/api/videos/{sample_video.id}/stream")
        assert response.status_code == 200
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == str(len(content))

    def test_stream_video_file_fallbacks_to_processed_path(self, client, db, sample_video, tmp_path):
        """测试流式播放优先读取 processed_filepath，避免原文件缺失时不可播。"""
        content = b"abcdef012345"
        processed_path = tmp_path / "stream-processed.mp4"
        processed_path.write_bytes(content)

        sample_video.filepath = str(tmp_path / "missing-original.mp4")
        sample_video.processed_filepath = str(processed_path)
        sample_video.filename = "stream-processed.mp4"
        db.commit()

        response = client.get(f"/api/videos/{sample_video.id}/stream")
        assert response.status_code == 200
        assert response.content == content

    def test_get_video_subtitles_returns_rows_sorted_by_start_time(self, client, db, sample_video):
        """测试字幕接口返回的数据库字幕按时间顺序排序"""
        from app.models.subtitle import Subtitle

        db.add_all(
            [
                Subtitle(
                    video_id=sample_video.id, start_time=12.0, end_time=14.0, text="第二句", source="asr", language="zh"
                ),
                Subtitle(
                    video_id=sample_video.id, start_time=2.0, end_time=4.0, text="第一句", source="asr", language="zh"
                ),
            ]
        )
        db.commit()

        response = client.get(f"/api/subtitles/videos/{sample_video.id}/subtitles")
        assert response.status_code == 200

        payload = response.json()
        assert [item["text"] for item in payload["subtitles"]] == ["第一句", "第二句"]
        assert payload["subtitles"][0]["start_time"] == 2.0

    def test_get_video_subtitles_falls_back_to_srt_file_when_db_rows_missing(self, client, db, sample_video, tmp_path):
        """测试旧视频缺少 subtitles 表数据时仍可从 SRT 文件回退读取字幕"""
        subtitle_path = tmp_path / "fallback.srt"
        subtitle_path.write_text(
            "1\n00:00:02,000 --> 00:00:05,000\n第一句字幕\n\n2\n00:00:08,500 --> 00:00:11,000\n第二句字幕\n",
            encoding="utf-8",
        )

        sample_video.subtitle_filepath = str(subtitle_path)
        db.commit()

        response = client.get(f"/api/subtitles/videos/{sample_video.id}/subtitles")
        assert response.status_code == 200

        payload = response.json()
        assert len(payload["subtitles"]) == 2
        assert payload["subtitles"][0]["text"] == "第一句字幕"
        assert payload["subtitles"][0]["start_time"] == 2.0
        assert payload["subtitles"][1]["end_time"] == 11.0

    def test_upload_video_file(self, client, db, tmp_path, monkeypatch, sample_user):
        """测试本地视频上传"""
        from app.core.config import settings
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(settings, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(settings, "TEMP_FOLDER", str(tmp_path / "temp"))
        monkeypatch.setattr(settings, "WHISPER_MODEL", "turbo")

        submitted = {}

        def fake_submit_task(task_func, *args, **kwargs):
            submitted["name"] = task_func.__name__
            submitted["args"] = args
            submitted["kwargs"] = kwargs
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)
        monkeypatch.setattr("app.routers.video.recommend_videos", fake_upload_recommendations)

        response = client.post(
            "/api/videos/upload",
            files={"file": ("lesson.mp4", io.BytesIO(b"fake-video-content"), "video/mp4")},
            data={"model": "small"},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["status"] == "pending"
        assert payload["duplicate"] is False
        assert payload["data"]["filename"] == "local-lesson.mp4"
        assert payload["data"]["requested_model"] == "small"
        assert payload["data"]["effective_model"] == "small"
        assert payload["recommendations"]["scene"] == "related"
        assert payload["recommendations"]["items"][0]["source_label"] == "站内视频"
        assert payload["recommendations"]["external_item_count"] == 0
        assert os.path.exists(payload["data"]["filepath"])
        assert payload["message"] == "视频上传成功，已开始后台处理"

        videos = db.query(Video).all()
        assert len(videos) == 1
        assert videos[0].status.value == "pending"
        assert videos[0].current_step == "已提交，等待处理（small）"
        assert submitted["name"] == "process_video_task"
        assert submitted["args"][0] == videos[0].id
        assert submitted["args"][2] == "small"

    def test_upload_video_file_duplicate(self, client, db, tmp_path, monkeypatch, sample_user):
        """测试重复上传同一视频"""
        from app.core.config import settings
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(settings, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(settings, "TEMP_FOLDER", str(tmp_path / "temp"))

        auth = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}
        files = {"file": ("duplicate.mp4", io.BytesIO(b"same-video"), "video/mp4")}
        first = client.post("/api/videos/upload", files=files, headers=auth)
        assert first.status_code == 200

        second = client.post(
            "/api/videos/upload",
            files={"file": ("duplicate.mp4", io.BytesIO(b"same-video"), "video/mp4")},
            headers=auth,
        )
        assert second.status_code == 200

        payload = second.json()
        assert payload["duplicate"] is True
        assert payload["message"] == "视频已存在"
        assert db.query(Video).count() == 1

    def test_upload_video_file_same_md5_distinct_users_not_duplicate(
        self, client, db, tmp_path, monkeypatch, sample_user
    ):
        """相同 MD5 在不同用户下各自入库，不得跨用户命中 duplicate。"""
        from app.core.config import settings
        from app.models.user import User
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(settings, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(settings, "TEMP_FOLDER", str(tmp_path / "temp"))
        monkeypatch.setattr("app.core.executor.submit_task", lambda *args, **kwargs: None)

        other = User(username="other_md5", email="other_md5@example.com", phone="13900139003", password="Other#7777")
        db.add(other)
        db.commit()
        db.refresh(other)

        body = io.BytesIO(b"identical-md5-for-local-upload")
        auth_a = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}
        auth_b = {"Authorization": f"Bearer {build_auth_token(other.id)}"}

        first = client.post(
            "/api/videos/upload",
            files={"file": ("first.mp4", body, "video/mp4")},
            data={"model": "small"},
            headers=auth_a,
        )
        body.seek(0)
        second = client.post(
            "/api/videos/upload",
            files={"file": ("second.mp4", body, "video/mp4")},
            data={"model": "small"},
            headers=auth_b,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is False
        assert first.json()["id"] != second.json()["id"]
        assert db.query(Video).count() == 2

    def test_upload_video_invalid_extension(self, client, sample_user):
        """测试上传不支持的文件类型"""
        from app.utils.auth_token import build_auth_token

        response = client.post(
            "/api/videos/upload",
            files={"file": ("document.txt", io.BytesIO(b"not-a-video"), "text/plain")},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "不支持的文件类型"

    def test_upload_video_invalid_content_type(self, client, sample_user):
        """测试伪装扩展名但非视频 MIME 会被拒绝。"""
        from app.utils.auth_token import build_auth_token

        response = client.post(
            "/api/videos/upload",
            files={"file": ("fake.mp4", io.BytesIO(b"not-a-video"), "text/plain")},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "文件内容类型无效，请上传视频文件"

    def test_upload_video_accepts_octet_stream_content_type(self, client, tmp_path, monkeypatch, sample_user):
        """测试兼容 iOS/浏览器可能上报的 octet-stream。"""
        from app.core.config import settings
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(settings, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(settings, "TEMP_FOLDER", str(tmp_path / "temp"))
        monkeypatch.setattr("app.core.executor.submit_task", lambda *args, **kwargs: None)

        response = client.post(
            "/api/videos/upload",
            files={"file": ("octet.mp4", io.BytesIO(b"fake-video-content"), "application/octet-stream")},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["duplicate"] is False

    def test_upload_video_url_creates_downloading_record(self, client, db, monkeypatch, sample_user):
        """测试链接上传会立即创建下载中记录"""
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        submitted = {}

        def fake_submit_task(task_func, *args, **kwargs):
            submitted["name"] = task_func.__name__
            submitted["args"] = args
            submitted["kwargs"] = kwargs
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)
        monkeypatch.setattr("app.routers.video.recommend_videos", fake_upload_recommendations)

        response = client.post(
            "/api/videos/upload-url",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "medium"},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["status"] == "downloading"
        assert payload["duplicate"] is False
        assert payload["data"]["status"] == "downloading"
        assert payload["data"]["requested_model"] == "medium"
        assert payload["recommendations"]["scene"] == "related"
        assert payload["recommendations"]["items"][0]["title"] == "站内导数复盘"
        assert payload["recommendations"]["external_item_count"] == 0

        video = db.query(Video).filter(Video.id == payload["id"]).first()
        assert video is not None
        assert video.status.value == "downloading"
        assert video.current_step == "已提交，等待下载（medium）"
        assert submitted["name"] == "download_video_from_url_task"
        assert submitted["args"][0] == video.id
        assert submitted["kwargs"]["model"] == "medium"

    def test_upload_video_url_recommendations_strip_slice_fields(self, client, monkeypatch, sample_user):
        """上传返回的 recommendations 出口也必须清空 summary/reason_text/tags。"""
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr("app.core.executor.submit_task", lambda *args, **kwargs: None)
        monkeypatch.setattr("app.routers.video.recommend_videos", fake_upload_recommendations)

        response = client.post(
            "/api/videos/upload-url",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "medium"},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        payload = response.json()
        item = payload["recommendations"]["items"][0]
        assert item["summary"] == ""
        assert item["tags"] == []
        assert item["reason_text"] == ""

    def test_upload_video_url_persists_recommendation_metadata(self, client, db, monkeypatch, sample_user):
        """测试链接上传可预填推荐候选标题、摘要与标签。"""
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        def fake_submit_task(task_func, *args, **kwargs):
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)

        response = client.post(
            "/api/videos/upload-url",
            json={
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "YouTube · Calculus Review",
                "summary": "适合配合当前数学主题继续学习。",
                "tags": ["数学", "导数"],
                "model": "medium",
            },
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        video = db.query(Video).filter(Video.id == payload["id"]).first()
        assert video is not None
        assert video.title == "YouTube · Calculus Review"
        assert video.summary == "适合配合当前数学主题继续学习。"
        assert json.loads(video.tags)[0] == "数学"

    def test_upload_video_url_duplicate_reuses_existing_video(self, client, db, monkeypatch, sample_user):
        """测试重复提交同一链接时复用已有视频记录。"""
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        def fake_submit_task(task_func, *args, **kwargs):
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)

        auth = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}
        payload = {"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "medium"}
        first = client.post("/api/videos/upload-url", json=payload, headers=auth)
        assert first.status_code == 200

        second = client.post("/api/videos/upload-url", json=payload, headers=auth)
        assert second.status_code == 200

        second_payload = second.json()
        first_payload = first.json()
        assert second_payload["duplicate"] is True
        assert second_payload["message"] == "该视频链接已提交过"
        assert second_payload["id"] == first_payload["id"]
        assert db.query(Video).count() == 1

    def test_upload_video_url_allows_resubmit_after_failed_record(self, client, db, monkeypatch, sample_user):
        """测试历史失败的链接任务不会阻止重新提交。"""
        from app.models.video import Video
        from app.models.video import VideoStatus
        from app.utils.auth_token import build_auth_token

        submitted = {"count": 0}

        def fake_submit_task(task_func, *args, **kwargs):
            submitted["count"] += 1
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)

        failed_video = Video(
            user_id=sample_user.id,
            title="旧失败链接",
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            status=VideoStatus.FAILED,
            current_step="旧任务失败",
        )
        db.add(failed_video)
        db.commit()
        db.refresh(failed_video)

        response = client.post(
            "/api/videos/upload-url",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "medium"},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["duplicate"] is False
        assert payload["id"] != failed_video.id
        assert submitted["count"] == 1
        assert db.query(Video).count() == 2

    def test_process_video_route_passes_non_base_model(self, client, sample_video, monkeypatch):
        """测试重新处理接口会把选中的模型传给后台任务。"""
        submitted = {}

        def fake_submit_task(task_func, *args, **kwargs):
            submitted["name"] = task_func.__name__
            submitted["args"] = args
            submitted["kwargs"] = kwargs
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)

        response = client.post(
            f"/api/videos/{sample_video.id}/process",
            json={"language": "Other", "model": "large", "auto_generate_summary": True, "auto_generate_tags": True},
        )
        assert response.status_code == 200

        status_response = client.get(f"/api/videos/{sample_video.id}/status")
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["requested_model"] == "large"
        assert status_payload["effective_model"] == "large"
        assert "large" in status_payload["current_step"]

        assert submitted["name"] == "process_video_task"
        assert submitted["args"][0] == sample_video.id
        assert submitted["args"][2] == "large"

    def test_get_video_processing_options_returns_model_catalog(self, client):
        """测试获取视频处理配置目录。"""
        response = client.get("/api/videos/processing-options")
        assert response.status_code == 200

        payload = response.json()
        assert "default_model" in payload
        assert "models" in payload
        assert isinstance(payload["models"], list)
        assert any(item["value"] == "base" for item in payload["models"])

    def test_generate_summary_from_transcript(self, client, monkeypatch):
        """测试本地转录文本也可以复用在线摘要生成逻辑。"""
        monkeypatch.setattr(
            "app.services.video_content_service.generate_video_summary",
            lambda *args, **kwargs: {
                "success": True,
                "summary": "这是基于本地转录文本生成的摘要。",
                "style": "study",
                "provider": "fallback",
            },
        )

        response = client.post(
            "/api/videos/generate-summary-from-transcript",
            json={
                "title": "本地视频",
                "transcript_text": "第一段内容。第二段内容。第三段内容。",
                "style": "study",
            },
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["success"] is True
        assert payload["summary"] == "这是基于本地转录文本生成的摘要。"
        assert payload["style"] == "study"

    def test_generate_summary_from_transcript_rejects_empty_text(self, client):
        """测试空转录文本不能生成摘要。"""
        response = client.post(
            "/api/videos/generate-summary-from-transcript",
            json={"title": "空文本", "transcript_text": "   ", "style": "study"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "转录文本为空，无法生成摘要"

    @pytest.mark.skip(reason="sync-offline-transcript 已从项目移除（返回 410 Gone）")
    def test_sync_offline_transcript_writes_same_video_table(self, client, db, monkeypatch, sample_user):
        """测试 iOS 本地离线转录结果可写入 videos 表并标记离线来源。"""
        import json

        from app.models.subtitle import Subtitle
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(
            "app.routers.video.generate_primary_topic_name",
            lambda *args, **kwargs: {"success": True, "name": "极限与连续核心梳理", "provider": "fallback"},
        )
        monkeypatch.setattr(
            "app.routers.video.generate_video_tags",
            lambda *args, **kwargs: {"success": True, "tags": ["数学", "极限", "连续函数"], "provider": "fallback"},
        )

        response = client.post(
            "/api/videos/sync-offline-transcript",
            json={
                "user_id": sample_user.id,
                "task_id": "local-task-001",
                "file_name": "lesson-local.mp4",
                "file_ext": "mp4",
                "file_size": 1024,
                "locale": "zh-CN",
                "engine": "apple_speech_on_device",
                "transcript_text": "先讲极限定义，再讲连续函数判定。",
                "summary": "本节重点讲解极限定义、连续函数判定与常见题型。",
                "summary_style": "study",
                "segments": [
                    {"text": "先讲极限定义", "start": 0, "duration": 3.5, "confidence": 0.9},
                    {"text": "再讲连续函数判定", "start": 3.5, "duration": 4.0, "confidence": 0.9},
                ],
            },
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["success"] is True
        assert payload["duplicate"] is False
        assert payload["video"]["processing_origin"] == "ios_offline"
        assert payload["video"]["processing_origin_label"] == "iOS 离线处理"
        assert payload["video"]["title"] == "极限与连续核心梳理"
        assert payload["video"]["task_id"] == "local-task-001"
        assert "数学" in payload["video"]["tags"]
        assert payload["video"]["tag_count"] == len(payload["video"]["tags"])

        video = db.query(Video).filter(Video.task_id == "local-task-001").first()
        assert video is not None
        assert video.title == "极限与连续核心梳理"
        assert video.summary == "本节重点讲解极限定义、连续函数判定与常见题型。"
        assert "数学" in json.loads(video.tags)
        assert video.status.value == "completed"

        subtitles = db.query(Subtitle).filter(Subtitle.video_id == video.id).order_by(Subtitle.start_time.asc()).all()
        assert len(subtitles) == 2
        assert subtitles[0].text == "先讲极限定义"

    @pytest.mark.skip(reason="sync-offline-transcript 已从项目移除（返回 410 Gone）")
    def test_sync_offline_transcript_updates_existing_record(self, client, db, monkeypatch, sample_user):
        """测试同一 task_id 的离线结果会更新原记录而不是重复插入。"""
        import json

        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(
            "app.routers.video.generate_primary_topic_name",
            lambda *args, **kwargs: {"success": True, "name": "导数题型总结", "provider": "fallback"},
        )

        payload = {
            "user_id": sample_user.id,
            "task_id": "local-task-002",
            "file_name": "derivative.mp4",
            "file_ext": "mp4",
            "file_size": 2048,
            "locale": "zh-CN",
            "engine": "apple_speech_on_device",
            "transcript_text": "第一版转录文本",
            "summary": "第一版摘要",
            "summary_style": "study",
            "segments": [],
        }
        auth = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}
        first = client.post("/api/videos/sync-offline-transcript", json=payload, headers=auth)
        assert first.status_code == 200

        second = client.post(
            "/api/videos/sync-offline-transcript",
            json={**payload, "summary": "更新后的摘要", "transcript_text": "更新后的转录文本"},
            headers=auth,
        )
        assert second.status_code == 200
        assert second.json()["duplicate"] is True

        videos = db.query(Video).filter(Video.task_id == "local-task-002").all()
        assert len(videos) == 1
        assert videos[0].summary == "更新后的摘要"
        assert isinstance(json.loads(videos[0].tags), list)
        assert "数学" in json.loads(videos[0].tags)

    def test_transcribe_audio_uses_backend_whisper(self, client, monkeypatch):
        """测试音频直传接口会调用后端 Whisper 并返回统一分段结构。"""
        monkeypatch.setattr(
            "app.routers.video.transcribe_audio_with_whisper",
            lambda *args, **kwargs: {
                "text": "这是后端 Whisper 返回的转录文本。",
                "segments": [
                    {"text": "这是后端 Whisper", "start": 0.0, "end": 1.8},
                    {"text": "返回的转录文本。", "start": 1.8, "end": 3.6},
                ],
            },
        )

        response = client.post(
            "/api/videos/transcribe-audio",
            files={"file": ("sample.m4a", io.BytesIO(b"fake audio bytes"), "audio/mp4")},
            data={"language": "Chinese", "model": "base"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["success"] is True
        assert payload["engine"] == "backend_whisper"
        assert payload["effective_model"] == "base"
        assert payload["effective_language"] == "zh"
        assert payload["transcript_text"] == "这是后端 Whisper 返回的转录文本。"
        assert len(payload["segments"]) == 2
        assert payload["segments"][0]["duration"] == pytest.approx(1.8)

    @pytest.mark.skip(reason="sync-offline-transcript 已从项目移除（返回 410 Gone）")
    def test_sync_offline_transcript_prefers_client_tags_when_present(self, client, db, monkeypatch, sample_user):
        """测试离线同步在显式传入 tags 时直接写入数据库。"""
        import json

        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr(
            "app.routers.video.generate_primary_topic_name",
            lambda *args, **kwargs: {"success": True, "name": "函数与单调性", "provider": "fallback"},
        )

        response = client.post(
            "/api/videos/sync-offline-transcript",
            json={
                "user_id": sample_user.id,
                "task_id": "local-task-003",
                "file_name": "function.mp4",
                "file_ext": "mp4",
                "file_size": 512,
                "locale": "zh-CN",
                "engine": "apple_speech_on_device",
                "transcript_text": "本节讲函数单调性与最值。",
                "summary": "重点分析函数单调性、区间判定和最值题型。",
                "summary_style": "study",
                "tags": ["函数", "单调性", "最值"],
                "auto_generate_tags": False,
                "segments": [],
            },
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        assert response.json()["video"]["tags"] == ["数学", "函数", "单调性", "最值"]

        video = db.query(Video).filter(Video.task_id == "local-task-003").first()
        assert video is not None
        assert json.loads(video.tags) == ["数学", "函数", "单调性", "最值"]


@pytest.mark.api
class TestNoteAPI:
    """笔记 API 测试"""

    def test_get_notes_empty(self, client):
        """测试获取空笔记列表"""
        response = client.get("/api/notes/notes")
        assert response.status_code == 200
        data = response.json()
        assert data["data"] == []

    def test_create_note(self, client, sample_video):
        """测试创建笔记"""
        response = client.post(
            "/api/notes/notes",
            json={
                "title": "新笔记",
                "content": "笔记内容",
                "video_id": sample_video.id,
                "tags": "导数, 极限, 导数",
                "timestamps": [
                    {
                        "time_seconds": 12.5,
                        "subtitle_text": "这是第一段重点字幕",
                    }
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["title"] == "新笔记"
        assert data["data"]["video_id"] == sample_video.id
        assert data["data"]["video_title"] == sample_video.title
        assert data["data"]["tags"] == ["导数", "极限"]
        assert len(data["data"]["timestamps"]) == 1
        assert data["data"]["timestamps"][0]["time_seconds"] == 12.5

    def test_create_note_without_title(self, client):
        """测试创建没有标题的笔记"""
        response = client.post(
            "/api/notes/notes",
            json={
                "title": "",
                "content": "内容",
            },
        )
        # FastAPI 返回 422 表示验证错误
        assert response.status_code in [400, 422]

    def test_get_note_by_id(self, client, sample_note):
        """测试通过 ID 获取笔记"""
        response = client.get(f"/api/notes/notes/{sample_note.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["id"] == sample_note.id

    def test_update_note(self, client, sample_note):
        """测试更新笔记"""
        response = client.put(
            f"/api/notes/notes/{sample_note.id}",
            json={
                "title": "更新后的标题",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["title"] == "更新后的标题"

    def test_update_note_video_and_clear_tags(self, client, db, sample_note, sample_video):
        """测试更新笔记的视频关联并清空标签。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        second_video = Video(
            user_id=sample_video.user_id,
            filename="second_video.mp4",
            filepath="/tmp/second_video.mp4",
            title="第二个测试视频",
            status=VideoStatus.COMPLETED,
        )
        db.add(second_video)
        db.commit()
        db.refresh(second_video)

        sample_note.tags = "旧标签,临时"
        db.commit()

        response = client.put(
            f"/api/notes/notes/{sample_note.id}",
            json={
                "video_id": second_video.id,
                "tags": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["video_id"] == second_video.id
        assert data["data"]["video_title"] == second_video.title
        assert data["data"]["tags"] == []

    def test_get_notes_with_filters(self, client, db, sample_video):
        """测试笔记列表支持 video/tag/search 筛选。"""
        from app.models.note import Note

        note = Note(
            title="函数极限笔记",
            content="这里记录函数极限与导数关系",
            video_id=sample_video.id,
            note_type="text",
            tags="极限,导数",
            keywords="极限,导数",
        )
        db.add(note)
        db.commit()

        response = client.get(
            "/api/notes/notes",
            params={"video_id": sample_video.id, "tag": "极限", "search": "函数极限"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["title"] == "函数极限笔记"
        assert data["data"][0]["video_title"] == sample_video.title

    def test_add_and_delete_note_timestamp(self, client, sample_note):
        """测试时间戳的新增与删除。"""
        create_response = client.post(
            f"/api/notes/notes/{sample_note.id}/timestamps",
            params={"time_seconds": 88.2, "subtitle_text": "勾股定理重点"},
        )
        assert create_response.status_code == 200
        created = create_response.json()["data"]
        assert created["time_seconds"] == 88.2
        assert created["subtitle_text"] == "勾股定理重点"

        delete_response = client.delete(f"/api/notes/notes/{sample_note.id}/timestamps/{created['id']}")
        assert delete_response.status_code == 200
        assert delete_response.json()["message"] == "时间戳已删除"

    def test_delete_note(self, client, sample_note):
        """测试删除笔记"""
        response = client.delete(f"/api/notes/notes/{sample_note.id}")
        assert response.status_code == 200

        # 验证删除
        response = client.get(f"/api/notes/notes/{sample_note.id}")
        assert response.status_code == 404


@pytest.mark.api
class TestAuthRequiredEndpoints:
    """需登录的写接口：默认仅信任 Bearer（见 AUTH_ALLOW_LEGACY_USER_ID_ONLY）。"""

    def test_upload_url_requires_bearer_token(self, client):
        response = client.post(
            "/api/videos/upload-url",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "small"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后再通过链接导入视频"

    def test_upload_file_requires_bearer_token(self, client):
        response = client.post(
            "/api/videos/upload",
            files={"file": ("lesson.mp4", io.BytesIO(b"fake-video-content"), "video/mp4")},
            data={"model": "small"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后再上传视频"

    @pytest.mark.skip(reason="sync-offline-transcript 已从项目移除（返回 410 Gone）")
    def test_sync_offline_requires_bearer_token(self, client):
        response = client.post(
            "/api/videos/sync-offline-transcript",
            json={
                "task_id": "unauth-offline-task",
                "transcript_text": "offline text",
            },
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后再同步离线转录结果"

    def test_upload_url_invalid_bearer_does_not_fallback_to_legacy_user_id(self, client, sample_user, monkeypatch):
        """legacy 开启时，若 Bearer 无效也不得回退到 user_id。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "AUTH_ALLOW_LEGACY_USER_ID_ONLY", True)
        response = client.post(
            "/api/videos/upload-url",
            params={"user_id": sample_user.id},
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "small"},
            headers={"Authorization": "Bearer invalid.token.signature"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后再通过链接导入视频"

    def test_upload_video_url_same_url_distinct_users_not_duplicate(self, client, db, monkeypatch, sample_user):
        """同一 URL 在不同用户下应各自建库，不因全局 URL 去重而误判 duplicate。"""
        from app.models.user import User
        from app.utils.auth_token import build_auth_token

        monkeypatch.setattr("app.core.executor.submit_task", lambda *args, **kwargs: None)

        other = User(
            username="other_url_user", email="other_url@example.com", phone="13900139002", password="Other#9999"
        )
        db.add(other)
        db.commit()
        db.refresh(other)

        payload = {"url": "https://www.bilibili.com/video/BV1isolated", "model": "medium"}
        first = client.post(
            "/api/videos/upload-url",
            json=payload,
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        second = client.post(
            "/api/videos/upload-url",
            json=payload,
            headers={"Authorization": f"Bearer {build_auth_token(other.id)}"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is False
        assert first.json()["id"] != second.json()["id"]


@pytest.mark.api
class TestAuthAPI:
    """认证 API 测试"""

    def test_register_user(self, client):
        """测试用户注册"""
        response = client.post(
            "/api/auth/register",
            json={
                "email": "new@example.com",
                "password": "Strong#123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["phone"] is None

    def test_register_user_with_phone(self, client):
        """测试手机号注册"""
        response = client.post(
            "/api/auth/register",
            json={
                "phone": "13800138001",
                "password": "Another#123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["phone"] == "13800138001"

    def test_register_duplicate_contact(self, client, sample_user):
        """测试重复邮箱注册"""
        response = client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "password": "Other#1234",
            },
        )
        assert response.status_code == 400

    def test_register_duplicate_password(self, client, sample_user):
        """测试重复密码注册被拦截"""
        response = client.post(
            "/api/auth/register",
            json={
                "email": "other@example.com",
                "password": "Strong#123",
            },
        )
        assert response.status_code == 400

    def test_login_success(self, client, sample_user):
        """测试登录成功"""
        response = client.post(
            "/api/auth/login",
            json={
                "account": "test@example.com",
                "password": "Strong#123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "user" in data
        assert data["token"]
        assert data["user"]["login_count"] == 1

    def test_login_success_with_phone(self, client, sample_user):
        """测试手机号登录成功"""
        response = client.post(
            "/api/auth/login",
            json={
                "account": "13800138000",
                "password": "Strong#123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["phone"] == "13800138000"

    def test_login_wrong_password(self, client, sample_user):
        """测试密码错误登录"""
        response = client.post(
            "/api/auth/login",
            json={
                "account": "test@example.com",
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 401

    def test_get_current_user_by_token(self, client, sample_user):
        """测试通过 token 获取当前用户"""
        login_response = client.post(
            "/api/auth/login",
            json={
                "account": "test@example.com",
                "password": "Strong#123",
            },
        )
        token = login_response.json()["token"]

        response = client.get("/api/auth/user", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["email"] == "test@example.com"
