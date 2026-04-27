"""API 测试 - 语义搜索接口。"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.api
def test_semantic_search_passes_include_tag_match_flag(client, db, sample_video, monkeypatch):
    """include_tag_match 应透传到语义搜索服务，便于本地验证标签增强检索。"""
    from app.models.video import VideoStatus

    sample_video.has_semantic_index = True
    sample_video.status = VideoStatus.COMPLETED
    db.commit()

    monkeypatch.setattr("app.routers.search.settings", MagicMock(SEARCH_ENABLED=True))

    captured = {}

    def fake_semantic_search_videos(**kwargs):
        captured["include_tag_match"] = kwargs.get("include_tag_match")
        return []

    monkeypatch.setattr("app.routers.search.semantic_search_videos", fake_semantic_search_videos)

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
    assert captured["include_tag_match"] is True
