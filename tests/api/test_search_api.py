"""API 测试 - 语义搜索接口。"""

from unittest.mock import MagicMock

import pytest


class SemanticSearchCallRecorder:
    def __init__(self):
        self.include_tag_match = None

    def __call__(self, **kwargs):
        self.include_tag_match = kwargs.get("include_tag_match")
        return []


@pytest.mark.api
def test_semantic_search_passes_include_tag_match_flag(client, db, sample_video, monkeypatch):
    """include_tag_match 应透传到语义搜索服务，便于本地验证标签增强检索。"""
    from app.models.video import VideoStatus

    sample_video.has_semantic_index = True
    sample_video.status = VideoStatus.COMPLETED
    db.commit()

    monkeypatch.setattr("app.routers.search.settings", MagicMock(SEARCH_ENABLED=True))

    recorder = SemanticSearchCallRecorder()
    monkeypatch.setattr("app.routers.search.semantic_search_videos", recorder)

    response = client.post(
        "/api/search/semantic/search",
        json={
            "query": "导数定义",
            "video_ids": [sample_video.id],
            "limit": 5,
            "threshold": 0.2,
            "include_tag_match": True,
        },
    )

    assert response.status_code == 200
    assert recorder.include_tag_match is True


@pytest.mark.api
def test_semantic_search_service_skips_soft_deleted_video(db, sample_video, monkeypatch):
    """服务层直接调用时也不能对软删除视频集合执行向量检索。"""
    from app.services.search import search as search_service

    sample_video.has_semantic_index = True
    sample_video.is_deleted = True
    db.commit()

    monkeypatch.setattr(
        search_service,
        "get_embedder",
        lambda *args, **kwargs: pytest.fail("deleted video should be filtered before embedding"),
    )

    results = search_service.semantic_search_videos(
        query="导数",
        video_ids=[sample_video.id],
        user_id=sample_video.user_id,
        db=db,
    )

    assert results == []


@pytest.mark.api
def test_semantic_search_rejects_soft_deleted_video_id_with_404(client, db, sample_video, monkeypatch):
    """显式搜索软删除视频时应按不存在处理，避免暴露删除记录。"""
    from app.models.video import VideoStatus

    monkeypatch.setattr("app.routers.search.settings", MagicMock(SEARCH_ENABLED=True))

    sample_video.has_semantic_index = True
    sample_video.status = VideoStatus.COMPLETED
    sample_video.is_deleted = True
    db.commit()

    response = client.post(
        "/api/search/semantic/search",
        json={
            "query": "导数定义",
            "video_ids": [sample_video.id],
            "limit": 5,
            "threshold": 0.2,
        },
    )

    assert response.status_code == 404
