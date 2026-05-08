"""冒烟测试 - 应用启动"""

import pytest


@pytest.mark.smoke
def test_app_can_start(client):
    """测试应用可以正常启动"""
    response = client.get("/")
    assert response.status_code in [200, 404]  # 根节点可能没有定义


@pytest.mark.smoke
def test_health_check(client):
    """测试健康检查端点"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "whisper" in data["services"]
    assert "ollama" in data["services"]


@pytest.mark.smoke
def test_docs_available(client):
    """测试 API 文档可访问"""
    response = client.get("/docs")
    assert response.status_code == 200


@pytest.mark.smoke
def test_openapi_schema(client):
    """测试 OpenAPI schema 可访问"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data
    assert "paths" in data


@pytest.mark.smoke
def test_video_list_endpoint(client):
    """测试视频列表端点"""
    response = client.get("/api/videos/list")
    assert response.status_code == 200


@pytest.mark.smoke
def test_note_list_endpoint(client):
    """测试笔记列表端点"""
    response = client.get("/api/notes/notes")
    assert response.status_code == 200
