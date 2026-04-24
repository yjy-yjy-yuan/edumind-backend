"""实时画面描述 API 测试。"""

import json
from unittest.mock import MagicMock, patch

import pytest


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
        MagicMock(FRAME_DESC_ENABLED=True),
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
