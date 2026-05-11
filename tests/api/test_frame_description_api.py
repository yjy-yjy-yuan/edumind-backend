"""实时画面描述 API 测试。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.frame_source_extractor import FrameSourceExtractionError


@pytest.mark.api
def test_describe_returns_503_when_disabled(client, sample_video, monkeypatch):
    """功能未启用时返回 503。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=False),
    )
    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": sample_video.id,
            "frames": ["/9j/4AAQSkZJRg=="],
            "timestamp": 10.0,
            "detail_level": "standard",
        },
    )
    assert response.status_code == 503
    assert "未启用" in response.json()["detail"]


@pytest.mark.api
def test_describe_returns_404_when_video_not_found(client, monkeypatch):
    """视频不存在时返回 404。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True, FRAME_DESC_ALLOW_EXTERNAL_VIDEO=False),
    )
    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": 99999,
            "frames": ["/9j/4AAQSkZJRg=="],
            "timestamp": 10.0,
            "detail_level": "standard",
        },
    )
    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


@pytest.mark.api
def test_describe_allows_external_video_for_isolated_local_qwen(client, monkeypatch):
    """本地 Qwen 联调可允许云端视频 ID，不因本地库缺记录而提前 404。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True, FRAME_DESC_ALLOW_EXTERNAL_VIDEO=True),
    )

    mock_service = MagicMock()

    def mock_describe(**kwargs):
        yield {
            "type": "complete",
            "stage": "completed",
            "full_description": "画面中正在播放云端视频",
            "timestamp": 10.0,
            "confidence": None,
            "context_summary": None,
            "degraded": False,
            "latency_ms": 456.0,
            "progress": 100,
            "message": "描述已完成",
        }

    mock_service.describe_frames.side_effect = mock_describe
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": 99999,
            "frames": ["/9j/4AAQSkZJRg=="],
            "timestamp": 10.0,
            "video_title": "云端短视频",
            "detail_level": "standard",
        },
    )

    assert response.status_code == 200
    assert "application/x-ndjson" in response.headers["content-type"]
    assert "画面中正在播放云端视频" in response.text
    assert mock_service.describe_frames.call_args.kwargs["video_title"] == "云端短视频"


@pytest.mark.api
def test_describe_can_use_server_frame_source_when_frontend_capture_is_blocked(client, monkeypatch):
    """iOS file:// WebView 无法 canvas 采帧时，可由本地实时描述后端按白名单抽帧。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(
            FRAME_DESC_ENABLED=True,
            FRAME_DESC_ALLOW_EXTERNAL_VIDEO=True,
            FRAME_DESC_ALLOW_SERVER_FRAME_FETCH=True,
            FRAME_DESC_SERVER_FRAME_ALLOWED_HOSTS="47.84.228.226",
        ),
    )
    monkeypatch.setattr(
        "app.routers.frame_description.extract_frame_from_video_url",
        lambda **kwargs: "/9j/server-side-frame==",
    )

    mock_service = MagicMock()

    def mock_describe(**kwargs):
        yield {
            "type": "complete",
            "stage": "completed",
            "full_description": "服务端抽帧后完成描述",
            "timestamp": 10.0,
            "confidence": None,
            "context_summary": None,
            "degraded": False,
            "latency_ms": 789.0,
            "progress": 100,
            "message": "描述已完成",
        }

    mock_service.describe_frames.side_effect = mock_describe
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": 99999,
            "frames": [],
            "frame_source_url": "https://47.84.228.226/api/videos/25/stream",
            "timestamp": 10.0,
            "video_title": "云端短视频",
            "detail_level": "standard",
        },
    )

    assert response.status_code == 200
    assert "正在服务端抽取视频帧" in response.text
    assert "服务端抽帧后完成描述" in response.text
    assert mock_service.describe_frames.call_args.kwargs["frames"] == ["/9j/server-side-frame=="]


@pytest.mark.api
def test_describe_continues_text_only_when_server_frame_source_fails(client, monkeypatch):
    """视频尾部服务端抽帧失败时，不把临时文件错误暴露给前端，继续走统一降级链。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(
            FRAME_DESC_ENABLED=True,
            FRAME_DESC_ALLOW_EXTERNAL_VIDEO=True,
            FRAME_DESC_ALLOW_SERVER_FRAME_FETCH=True,
            FRAME_DESC_SERVER_FRAME_ALLOWED_HOSTS="47.84.228.226",
        ),
    )

    def raise_extraction_error(**kwargs):
        raise FrameSourceExtractionError("服务端抽帧结果不是有效图片")

    monkeypatch.setattr(
        "app.routers.frame_description.extract_frame_from_video_url",
        raise_extraction_error,
    )

    mock_service = MagicMock()

    def mock_describe(**kwargs):
        yield {
            "type": "complete",
            "stage": "completed",
            "full_description": "已改用文本上下文继续描述",
            "timestamp": 75.0,
            "confidence": None,
            "context_summary": None,
            "degraded": True,
            "latency_ms": 12.0,
            "progress": 100,
            "message": "描述已完成",
        }

    mock_service.describe_frames.side_effect = mock_describe
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": 99999,
            "frames": [],
            "frame_source_url": "https://47.84.228.226/api/videos/25/stream",
            "timestamp": 75.0,
            "video_title": "云端短视频",
            "detail_level": "standard",
        },
    )

    assert response.status_code == 200
    assert "已改用文本上下文继续描述" in response.text
    assert "server_frame_extract_failed" not in response.text
    assert "cannot identify image file" not in response.text
    assert mock_service.describe_frames.call_args.kwargs["frames"] == []


@pytest.mark.api
def test_describe_stream_returns_ndjson(client, sample_video, monkeypatch):
    """正常流式调用返回 NDJSON。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )

    # Mock 服务以避免真实调用
    mock_service = MagicMock()

    def mock_describe(**kwargs):
        yield {
            "type": "status",
            "stage": "connecting",
            "message": "正在连接画面描述服务",
            "progress": 5,
        }
        yield {
            "type": "description",
            "delta": "老师在黑板上写字",
            "timestamp": 10.0,
            "confidence": None,
        }
        yield {
            "type": "complete",
            "stage": "completed",
            "full_description": "老师在黑板上写字",
            "timestamp": 10.0,
            "confidence": None,
            "context_summary": None,
            "degraded": False,
            "latency_ms": 123.5,
            "progress": 100,
            "message": "描述已完成",
        }

    mock_service.describe_frames.side_effect = mock_describe

    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": sample_video.id,
            "frames": ["/9j/4AAQSkZJRg=="],
            "timestamp": 10.0,
            "detail_level": "standard",
        },
    )

    assert response.status_code == 200
    assert "application/x-ndjson" in response.headers["content-type"]

    lines = [line for line in response.text.splitlines() if line.strip()]
    assert len(lines) >= 2

    events = [json.loads(line) for line in lines]
    assert events[0]["type"] == "status"
    desc_events = [e for e in events if e["type"] == "description"]
    assert len(desc_events) >= 1
    complete_events = [e for e in events if e["type"] == "complete"]
    assert len(complete_events) == 1


@pytest.mark.api
def test_session_start_creates_active_session(client, sample_video, monkeypatch):
    """开启会话返回 active 状态。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )

    mock_service = MagicMock()
    mock_service.start_session.return_value = {
        "session_id": "sess-123",
        "status": "active",
        "message": "实时描述会话已开启",
        "detail_level": "standard",
    }
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/session",
        json={
            "video_id": sample_video.id,
            "action": "start",
            "detail_level": "standard",
            "session_id": "",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["session_id"] == "sess-123"


@pytest.mark.api
def test_session_stop_requires_session_id(client, sample_video, monkeypatch):
    """关闭会话缺少 session_id 时返回 400。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )

    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/session",
        json={
            "video_id": sample_video.id,
            "action": "stop",
            "session_id": "",
        },
    )
    assert response.status_code == 400


@pytest.mark.api
def test_session_stop_returns_stopped(client, sample_video, monkeypatch):
    """关闭会话返回 stopped 状态。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )

    mock_service = MagicMock()
    mock_service.stop_session.return_value = {
        "session_id": "sess-123",
        "status": "stopped",
        "message": "实时描述会话已关闭",
        "detail_level": "",
    }
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/session",
        json={
            "video_id": sample_video.id,
            "action": "stop",
            "session_id": "sess-123",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"


@pytest.mark.api
def test_health_returns_enabled_flag(client, monkeypatch):
    """健康检查端点返回服务状态。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.get("/api/frame_description/health")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "service" in data


@pytest.mark.api
def test_session_disabled_returns_503(client, sample_video, monkeypatch):
    """功能未启用时 session 端点返回 503。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=False),
    )
    response = client.post(
        "/api/frame_description/session",
        json={
            "video_id": sample_video.id,
            "action": "start",
            "detail_level": "standard",
            "session_id": "",
        },
    )
    assert response.status_code == 503
    assert "未启用" in response.json()["detail"]


@pytest.mark.api
def test_health_disabled_returns_enabled_false(client, monkeypatch):
    """健康检查端点在功能未启用时返回 enabled=False。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=False),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )
    response = client.get("/api/frame_description/health")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["description"] == "功能未启用"


@pytest.mark.api
def test_invalid_action_returns_422(client, sample_video, monkeypatch):
    """非法 action 值被 Pydantic 验证拒绝（422）。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )

    response = client.post(
        "/api/frame_description/session",
        json={
            "video_id": sample_video.id,
            "action": "restart",  # 非法值，Pydantic 返回 422
            "session_id": "",
        },
    )
    assert response.status_code == 422


@pytest.mark.api
def test_health_returns_vinci_probe_result(client, monkeypatch):
    """健康检查端点包含 Vinci 实际可达性探测结果。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )
    # Mock VinciAdapterService.health_check to avoid real HTTP calls
    from app.services.vinci_adapter_service import VinciHealthResult

    mock_health = VinciHealthResult(reachable=True, latency_ms=42.5)
    monkeypatch.setattr(
        "app.services.vinci_adapter_service.VinciAdapterService.health_check",
        lambda self, **kw: mock_health,
    )

    response = client.get("/api/frame_description/health")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert "vinci" in data
    assert "reachable" in data["vinci"]
    assert "latency_ms" in data["vinci"]
    assert "error" in data["vinci"]
    assert "error_code" in data["vinci"]
    assert data["vinci"]["reachable"] is True
    assert data["vinci"]["latency_ms"] == 42.5


@pytest.mark.api
def test_health_vinci_unreachable_includes_error_detail(client, monkeypatch):
    """Vinci 不可达时 health 返回错误详情。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )
    from app.services.vinci_adapter_service import VinciHealthResult

    mock_health = VinciHealthResult(
        reachable=False,
        latency_ms=-1.0,
        error="connection refused",
        error_code="VINCI_UNAVAILABLE",
    )
    monkeypatch.setattr(
        "app.services.vinci_adapter_service.VinciAdapterService.health_check",
        lambda self, **kw: mock_health,
    )

    response = client.get("/api/frame_description/health")
    assert response.status_code == 200
    data = response.json()
    assert data["vinci"]["reachable"] is False
    assert data["vinci"]["error"] == "connection refused"
    assert data["vinci"]["error_code"] == "VINCI_UNAVAILABLE"


@pytest.mark.api
def test_health_returns_qwen3vl_probe_result(client, monkeypatch):
    """健康检查端点在 qwen3vl 后端下返回当前上游状态。"""
    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True, FRAME_DESC_BACKEND="qwen3vl"),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: mock_service,
    )
    from app.services.qwen3vl_realtime_client import Qwen3VLHealthResult

    mock_health = Qwen3VLHealthResult(
        reachable=True,
        latency_ms=12.5,
        loaded=False,
        model="Qwen/Qwen3-VL-2B-Instruct",
        device="mps",
    )
    monkeypatch.setattr(
        "app.routers.frame_description.Qwen3VLRealtimeClient.health_check",
        lambda self, **kw: mock_health,
    )

    response = client.get("/api/frame_description/health")

    assert response.status_code == 200
    data = response.json()
    assert data["upstream"]["provider"] == "qwen3vl"
    assert data["upstream"]["reachable"] is True
    assert data["upstream"]["loaded"] is False
    assert data["upstream"]["model"] == "Qwen/Qwen3-VL-2B-Instruct"
    assert data["upstream"]["device"] == "mps"


@pytest.mark.api
def test_describe_service_error_with_allow_degrade_returns_degraded_complete(client, sample_video, monkeypatch):
    """服务层抛错时，allow_degrade=True 应返回降级 complete 事件，而非仅 error。"""
    from app.services.frame_description_service import FrameDescServiceError

    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )

    class _FailingService:
        def describe_frames(self, **kwargs):
            raise FrameDescServiceError("vinci_probe_unreachable:VINCI_UNAVAILABLE:connection refused")
            yield  # pragma: no cover

    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: _FailingService(),
    )

    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": sample_video.id,
            "frames": ["/9j/4AAQSkZJRg=="],
            "timestamp": 10.0,
            "detail_level": "standard",
            "allow_degrade": True,
        },
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert any(evt.get("type") == "complete" and evt.get("degraded") is True for evt in events)
    assert any(evt.get("type") == "description" for evt in events)


@pytest.mark.api
def test_describe_service_error_without_degrade_returns_error_event(client, sample_video, monkeypatch):
    """服务层抛错且 allow_degrade=False 时应返回 error 事件。"""
    from app.services.frame_description_service import FrameDescServiceError

    monkeypatch.setattr(
        "app.routers.frame_description.settings",
        MagicMock(FRAME_DESC_ENABLED=True),
    )

    class _FailingService:
        def describe_frames(self, **kwargs):
            raise FrameDescServiceError("vinci_probe_unreachable")
            yield  # pragma: no cover

    monkeypatch.setattr(
        "app.routers.frame_description.get_frame_desc_service",
        lambda: _FailingService(),
    )

    response = client.post(
        "/api/frame_description/describe",
        json={
            "video_id": sample_video.id,
            "frames": ["/9j/4AAQSkZJRg=="],
            "timestamp": 10.0,
            "detail_level": "standard",
            "allow_degrade": False,
        },
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert any(evt.get("type") == "error" for evt in events)
