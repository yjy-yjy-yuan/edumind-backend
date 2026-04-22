"""API 测试 - 推荐接口。"""

import json
import logging

import pytest
from app.services import video_recommendation_service as recommendation_service
from app.services.external_candidate_service import ExternalCandidate
from app.services.external_candidate_service import ExternalCandidateFetchReport
from app.services.external_candidate_service import ExternalProviderFetchSummary


@pytest.fixture(autouse=True)
def reset_recommendation_ops_event_store():
    """隔离推荐运营聚合内存缓冲，避免测试间互相污染。"""
    from app.services.recommendation_ops_service import reset_recommendation_event_store_for_tests

    reset_recommendation_event_store_for_tests()
    yield
    reset_recommendation_event_store_for_tests()


def fake_fetch_external_candidates_report(*args, **kwargs):
    """返回推荐接口测试用的站外候选假数据。"""
    return ExternalCandidateFetchReport(
        candidates=[
            ExternalCandidate(
                id="youtube:abc123",
                provider="youtube",
                source_label="YouTube",
                title="YouTube · Calculus Review",
                external_url="https://www.youtube.com/watch?v=abc123",
                summary="适合配合当前数学主题继续学习。",
                tags=["数学", "导数"],
                subject="数学",
                primary_topic="导数",
                cluster_key="数学::导数",
            )
        ],
        providers=[
            ExternalProviderFetchSummary(
                provider="youtube",
                source_label="YouTube",
                status="success",
                candidate_count=1,
                latency_ms=145,
            ),
            ExternalProviderFetchSummary(
                provider="icourse163",
                source_label="中国大学慕课",
                status="failed",
                candidate_count=0,
                error_message="search failed",
                latency_ms=620,
            ),
        ],
    )


@pytest.mark.api
class TestRecommendationAPI:
    """推荐 API 测试。"""

    def test_get_recommendation_scenes(self, client):
        """测试获取推荐场景列表。"""
        response = client.get("/api/recommendations/scenes", headers={"X-Trace-Id": "upstream-trace-1"})
        assert response.status_code == 200
        assert response.headers.get("X-Trace-Id") == "upstream-trace-1"

        payload = response.json()
        assert payload["message"] == "获取推荐场景成功"
        assert {item["value"] for item in payload["scenes"]} >= {"home", "continue", "review", "related"}

    def test_videos_contract_version_and_trace_headers(self, client):
        """P1-C017 / P1-C016：响应含 contract_version，响应头含 X-Trace-Id。"""
        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 1},
            headers={"X-Trace-Id": "reco-trace-2"},
        )
        assert response.status_code == 200
        assert response.headers.get("X-Trace-Id") == "reco-trace-2"
        payload = response.json()
        assert payload.get("contract_version") == "2"
        assert payload.get("message") == "获取推荐视频成功"

    def test_videos_items_strip_slice_fields(self, client, db, sample_user, monkeypatch):
        """推荐接口出口统一去切片：summary/reason_text/tags 必须清空。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        internal_video = Video(
            user_id=sample_user.id,
            title="高数导数专题",
            filename="math.mp4",
            filepath="/tmp/math.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="围绕导数定义与函数单调性做复盘。",
            tags='["导数","函数"]',
        )
        db.add(internal_video)
        db.commit()

        monkeypatch.setattr(
            recommendation_service,
            "fetch_external_candidates_report",
            fake_fetch_external_candidates_report,
        )

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 4, "include_external": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["items"]
        assert all(item["summary"] == "" for item in payload["items"])
        assert all(item["tags"] == [] for item in payload["items"])
        assert all(item["reason_text"] == "" for item in payload["items"])

    def test_videos_contract_v1_restores_seed_video_title(self, client, db, sample_user, monkeypatch):
        """RECOMMENDATION_CONTRACT_VERSION=1 时仍返回 seed_video_title，但切片文本字段继续清空。"""
        from app.core.config import settings
        from app.models.video import Video
        from app.models.video import VideoStatus

        seed_video = Video(
            user_id=sample_user.id,
            title="v1 种子标题",
            filename="seed.mp4",
            filepath="/tmp/seed.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="讲解导数在函数单调性判断中的应用。",
            tags='["导数","单调性","函数"]',
        )
        best_match = Video(
            user_id=sample_user.id,
            title="函数单调性题型精讲",
            filename="best-match.mp4",
            filepath="/tmp/best-match.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="围绕函数、导数和单调性题型做系统复盘。",
            tags='["导数","函数","题型"]',
        )
        db.add_all([seed_video, best_match])
        db.commit()

        monkeypatch.setattr(settings, "RECOMMENDATION_CONTRACT_VERSION", "1", raising=False)

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "related", "seed_video_id": seed_video.id, "limit": 2},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["contract_version"] == "1"
        assert payload["seed_video_title"] == seed_video.title
        assert payload["items"][0]["reason_code"] == "related"
        assert payload["items"][0]["reason_text"] == ""

    def test_home_recommendations_prioritize_active_video(self, client, db, sample_user):
        """首页推荐优先返回当前需要继续跟进的处理中视频。"""
        from app.models.video import Video
        from app.models.video import VideoStatus
        from app.utils.auth_token import build_auth_token

        sample_user.learning_direction = "数学 导数"
        db.commit()

        processing_video = Video(
            user_id=sample_user.id,
            title="导数应用串讲",
            filename="processing.mp4",
            filepath="/tmp/processing.mp4",
            status=VideoStatus.PROCESSING,
            process_progress=56,
            current_step="语音识别中",
            summary="",
            tags='["导数","真题"]',
        )
        completed_video = Video(
            user_id=sample_user.id,
            title="英语写作结构梳理",
            filename="english.mp4",
            filepath="/tmp/english.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="总结英语写作常见结构。",
            tags='["英语","写作"]',
        )
        related_interest_video = Video(
            user_id=sample_user.id,
            title="函数极限与导数复盘",
            filename="math-review.mp4",
            filepath="/tmp/math-review.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="围绕极限、导数定义与常见求导法则做复盘。",
            tags='["导数","极限","函数"]',
        )
        db.add_all([processing_video, completed_video, related_interest_video])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 3},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["scene"] == "home"
        assert payload["strategy"] == "video_status_interest_v1"
        assert payload["personalized"] is True
        assert 2 <= len(payload["items"]) <= 3
        assert payload["items"][0]["id"] == processing_video.id
        assert payload["items"][0]["reason_code"] == "continue"
        assert payload["items"][0]["tags"] == []
        assert payload["items"][0]["summary"] == ""
        assert payload["items"][0]["reason_text"] == ""
        assert any(item["reason_code"] in {"interest", "continue"} for item in payload["items"])

    def test_home_recommendations_enforce_similarity_threshold_and_window(self, client, db, sample_user):
        """推荐接口应按后端阈值过滤，并将条数规范化到 6~8。"""
        from app.models.video import Video
        from app.models.video import VideoStatus
        from app.utils.auth_token import build_auth_token

        math_videos = [
            Video(
                user_id=sample_user.id,
                title=f"数学导数专题 {idx}",
                filename=f"math-{idx}.mp4",
                filepath=f"/tmp/math-{idx}.mp4",
                status=VideoStatus.COMPLETED,
                summary="围绕导数、函数与极限进行同主题复盘。",
                tags='["数学","导数","函数"]',
            )
            for idx in range(6)
        ]
        english_videos = [
            Video(
                user_id=sample_user.id,
                title=f"英语听力技巧 {idx}",
                filename=f"english-{idx}.mp4",
                filepath=f"/tmp/english-{idx}.mp4",
                status=VideoStatus.COMPLETED,
                summary="整理英语听力高频信号词与定位方法。",
                tags='["英语","听力","语法"]',
            )
            for idx in range(3)
        ]
        db.add_all([*math_videos, *english_videos])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 2, "include_external": False},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert 6 <= len(payload["items"]) <= 8
        assert all(item["item_type"] == "video" for item in payload["items"])
        assert all("数学导数专题" in str(item["title"]) for item in payload["items"])
        assert all(item["summary"] == "" for item in payload["items"])
        assert all(item["tags"] == [] for item in payload["items"])
        assert all(item["reason_text"] == "" for item in payload["items"])

    def test_related_recommendations_require_seed_video(self, client):
        """相关推荐场景必须传 seed_video_id。"""
        response = client.get("/api/recommendations/videos", params={"scene": "related"})
        assert response.status_code == 422
        assert response.json()["detail"] == "scene=related 时必须传入 seed_video_id"

    def test_related_recommendations_rank_overlap_video_first(self, client, db, sample_user):
        """相关推荐优先返回与 seed 视频主题重合度更高的视频。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        seed_video = Video(
            user_id=sample_user.id,
            title="导数与单调性",
            filename="seed.mp4",
            filepath="/tmp/seed.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="讲解导数在函数单调性判断中的应用。",
            tags='["导数","单调性","函数"]',
        )
        best_match = Video(
            user_id=sample_user.id,
            title="函数单调性题型精讲",
            filename="best-match.mp4",
            filepath="/tmp/best-match.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="围绕函数、导数和单调性题型做系统复盘。",
            tags='["导数","函数","题型"]',
        )
        other_video = Video(
            user_id=sample_user.id,
            title="英语听力技巧",
            filename="english-listening.mp4",
            filepath="/tmp/english-listening.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="总结英语听力中的关键信号词。",
            tags='["英语","听力"]',
        )
        db.add_all([seed_video, best_match, other_video])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "related", "seed_video_id": seed_video.id, "limit": 2},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["seed_video_id"] == seed_video.id
        assert "seed_video_title" not in payload
        assert payload["items"][0]["id"] == best_match.id
        assert payload["items"][0]["reason_code"] == "related"
        assert payload["items"][0]["tags"] == []
        assert payload["items"][0]["summary"] == ""
        assert payload["items"][0]["reason_text"] == ""
        assert all(item["id"] != seed_video.id for item in payload["items"])

    def test_related_recommendations_infer_subject_from_title_only(self, client, db, sample_user):
        """即使原始标签为空，相关推荐也应能根据科目归一返回同科目内容。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        seed_video = Video(
            user_id=sample_user.id,
            title="牛顿第二定律串讲",
            filename="seed-physics.mp4",
            filepath="/tmp/seed-physics.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="高中物理受力分析与牛顿第二定律综合应用。",
            tags=None,
        )
        physics_match = Video(
            user_id=sample_user.id,
            title="受力分析复习",
            filename="physics-match.mp4",
            filepath="/tmp/physics-match.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="物理力学里常见的受力分析步骤整理。",
            tags=None,
        )
        english_video = Video(
            user_id=sample_user.id,
            title="英语听力技巧",
            filename="english-listening.mp4",
            filepath="/tmp/english-listening.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="整理英语听力高频信号词。",
            tags='["英语听力"]',
        )
        db.add_all([seed_video, physics_match, english_video])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "related", "seed_video_id": seed_video.id, "limit": 2},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["items"][0]["id"] == physics_match.id
        assert payload["items"][0]["subject"] == "物理"
        assert payload["items"][0]["tags"] == []
        assert payload["items"][0]["reason_text"] == ""
        assert payload["items"][0]["reason_code"] in {"related", "subject"}

    def test_related_recommendations_hard_filter_seed_and_excluded_ids(self, client, db, sample_user, monkeypatch):
        """路由层在最终响应前应硬过滤 seed 与 exclude_video_ids 指定视频。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        seed_video = Video(
            user_id=sample_user.id,
            title="种子视频",
            filename="seed.mp4",
            filepath="/tmp/seed.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
        )
        excluded_video = Video(
            user_id=sample_user.id,
            title="应排除视频",
            filename="excluded.mp4",
            filepath="/tmp/excluded.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
        )
        kept_video = Video(
            user_id=sample_user.id,
            title="应保留视频",
            filename="kept.mp4",
            filepath="/tmp/kept.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
        )
        db.add_all([seed_video, excluded_video, kept_video])
        db.commit()
        db.refresh(seed_video)
        db.refresh(excluded_video)
        db.refresh(kept_video)

        def fake_recommend_videos(**kwargs):
            return {
                "message": "mock",
                "contract_version": "1",
                "scene": "related",
                "strategy": "mock",
                "personalized": False,
                "fallback_used": False,
                "seed_video_id": seed_video.id,
                "seed_video_title": seed_video.title,
                "internal_item_count": 3,
                "external_item_count": 0,
                "external_failed_provider_count": 0,
                "external_fetch_failed": False,
                "flow_version": "recommendation_flow_v1",
                "auto_materialized_external_count": 0,
                "auto_materialization_failed_count": 0,
                "sources": [{"source_type": "internal", "provider": "internal", "source_label": "视频库", "count": 3}],
                "external_query": None,
                "external_providers": [],
                "items": [
                    {"id": seed_video.id, "title": "seed"},
                    {"id": excluded_video.id, "title": "excluded"},
                    {"id": kept_video.id, "title": "kept"},
                ],
            }

        monkeypatch.setattr("app.routers.recommendation.recommend_videos", fake_recommend_videos)

        response = client.get(
            "/api/recommendations/videos",
            params={
                "scene": "related",
                "seed_video_id": seed_video.id,
                "exclude_video_ids": str(excluded_video.id),
                "include_external": False,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["items"]] == [kept_video.id]
        assert payload["internal_item_count"] == 1
        assert payload["external_item_count"] == 0
        assert payload["sources"][0]["count"] == 1

    def test_recommendations_exclude_blocked_title_keyword(self, client, db, sample_user, monkeypatch):
        """命中黑名单标题关键词（排列组合插空法详解）时，应从最终响应中剔除并重算计数。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        kept_video = Video(
            user_id=sample_user.id,
            title="函数单调性题型精讲",
            filename="kept.mp4",
            filepath="/tmp/kept.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
        )
        db.add(kept_video)
        db.commit()
        db.refresh(kept_video)

        def fake_recommend_videos(**kwargs):
            return {
                "message": "mock",
                "contract_version": "2",
                "scene": "home",
                "strategy": "mock",
                "personalized": False,
                "fallback_used": False,
                "seed_video_id": None,
                "internal_item_count": 2,
                "external_item_count": 0,
                "external_failed_provider_count": 0,
                "external_fetch_failed": False,
                "flow_version": "recommendation_flow_v1",
                "auto_materialized_external_count": 0,
                "auto_materialization_failed_count": 0,
                "sources": [{"source_type": "internal", "provider": "internal", "source_label": "视频库", "count": 2}],
                "external_query": None,
                "external_providers": [],
                "items": [
                    {"id": 991, "title": "排列组合插空法详解"},
                    {"id": kept_video.id, "title": "函数单调性题型精讲"},
                ],
            }

        monkeypatch.setattr("app.routers.recommendation.recommend_videos", fake_recommend_videos)

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 6, "include_external": False},
        )
        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["items"]] == [kept_video.id]
        assert payload["internal_item_count"] == 1
        assert payload["external_item_count"] == 0

    def test_home_recommendations_include_external_candidates(self, client, db, sample_user, monkeypatch):
        """推荐接口应支持在返回中混入站外候选元数据。"""
        from app.models.video import Video
        from app.models.video import VideoStatus

        internal_video = Video(
            user_id=sample_user.id,
            title="高数导数专题",
            filename="math.mp4",
            filepath="/tmp/math.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="围绕导数定义与函数单调性做复盘。",
            tags='["导数","函数"]',
        )
        db.add(internal_video)
        db.commit()

        monkeypatch.setattr(
            recommendation_service,
            "fetch_external_candidates_report",
            fake_fetch_external_candidates_report,
        )

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 4, "include_external": True},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["strategy"] == "video_status_interest_external_v2"
        assert any(item["id"] == internal_video.id for item in payload["items"])
        assert payload["internal_item_count"] == 1
        assert payload["external_item_count"] == 1
        assert payload["external_failed_provider_count"] == 1
        assert payload["external_fetch_failed"] is True
        assert payload["external_query"]["subject"] == "数学"
        assert any(item["provider"] == "internal" for item in payload["sources"])
        assert any(item["provider"] == "youtube" for item in payload["sources"])
        assert any(
            item["provider"] == "icourse163" and item["status"] == "failed" for item in payload["external_providers"]
        )
        external_item = next(item for item in payload["items"] if item.get("is_external") is True)
        assert external_item["source_label"] == "YouTube"
        assert external_item["external_url"] == "https://www.youtube.com/watch?v=abc123"
        assert external_item["item_type"] == "external_candidate"
        assert external_item["can_import"] is True
        assert external_item["action_api"] == "/api/recommendations/import-external"
        assert external_item["action_method"] == "POST"
        assert external_item["action_type"] == "import_external_url"
        assert external_item["action_target"].startswith("/upload?mode=url&url=")
        assert external_item["summary"] == ""
        assert external_item["tags"] == []
        assert external_item["reason_text"] == ""
        assert all(item["summary"] == "" for item in payload["items"])
        assert all(item["tags"] == [] for item in payload["items"])
        assert all(item["reason_text"] == "" for item in payload["items"])

    def test_home_recommendations_auto_materialize_external_for_authenticated_user(
        self, client, db, sample_user, monkeypatch
    ):
        """登录用户命中站外可导入候选时，应先入库再返回可打开详情的推荐项。"""
        from app.core.config import settings
        from app.models.video import Video
        from app.models.video import VideoStatus
        from app.utils.auth_token import build_auth_token

        internal_video = Video(
            user_id=sample_user.id,
            title="高数导数专题",
            filename="math.mp4",
            filepath="/tmp/math.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="分析完成",
            summary="围绕导数定义与函数单调性做复盘。",
            tags='["导数","函数"]',
        )
        db.add(internal_video)
        db.commit()

        monkeypatch.setattr(settings, "RECOMMENDATION_AUTO_IMPORT_EXTERNAL", True)
        monkeypatch.setattr(settings, "RECOMMENDATION_AUTO_IMPORT_MAX_ITEMS", 1)
        monkeypatch.setattr(
            recommendation_service,
            "fetch_external_candidates_report",
            fake_fetch_external_candidates_report,
        )
        monkeypatch.setattr("app.core.executor.submit_task", lambda *args, **kwargs: None)

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 4, "include_external": True},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["flow_version"] == "recommendation_flow_v2"
        assert payload["auto_materialized_external_count"] == 1
        assert payload["auto_materialization_failed_count"] == 0
        assert payload["external_item_count"] == 0
        assert payload["internal_item_count"] >= 2
        materialized_item = next(item for item in payload["items"] if item.get("materialized_from_external") is True)
        assert materialized_item["is_external"] is False
        assert materialized_item["item_type"] == "video"
        assert materialized_item["action_type"] == "open_video_detail"
        assert materialized_item["action_target"].startswith("/videos/")
        assert materialized_item["materialization_status"] in {"created", "reused"}
        assert materialized_item["summary"] == ""
        assert materialized_item["tags"] == []
        assert materialized_item["reason_text"] == ""

        created = (
            db.query(Video)
            .filter(Video.user_id == sample_user.id, Video.url == "https://www.youtube.com/watch?v=abc123")
            .first()
        )
        assert created is not None
        assert created.status.value == "downloading"

    def test_import_external_recommendation_creates_downloading_record(self, client, db, sample_user, monkeypatch):
        """推荐候选应能直接走后端链接下载入库链路。"""
        from app.models.video import Video
        from app.utils.auth_token import build_auth_token

        submitted = {}

        def fake_submit_task(task_func, *args, **kwargs):
            submitted["name"] = task_func.__name__
            submitted["args"] = args
            submitted["kwargs"] = kwargs
            return None

        monkeypatch.setattr("app.core.executor.submit_task", fake_submit_task)

        response = client.post(
            "/api/recommendations/import-external",
            json={
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": "B站·导数与函数单调性综合串讲",
                "summary": "适合配合当前数学主题继续学习。",
                "tags": ["数学", "导数", "函数"],
                "model": "small",
            },
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["status"] == "downloading"
        assert payload["duplicate"] is False
        assert payload["data"]["title"] == "B站·导数与函数单调性综合串讲"
        assert payload["data"]["summary"] == "适合配合当前数学主题继续学习。"
        assert payload["data"]["tags"][0] == "数学"

        video = db.query(Video).filter(Video.id == payload["id"]).first()
        assert video is not None
        assert video.url == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert video.status.value == "downloading"
        assert submitted["name"] == "download_video_from_url_task"
        assert submitted["args"][0] == video.id
        assert submitted["kwargs"]["model"] == "small"

    def test_import_external_requires_bearer_token(self, client):
        """未携带 Bearer 时不得仅凭 body/query 冒用 user_id（应 401）。"""
        response = client.post(
            "/api/recommendations/import-external",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "small"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后再导入站外视频"

    def test_import_external_invalid_bearer_does_not_fallback_to_legacy_user_id(self, client, sample_user, monkeypatch):
        """legacy 开启时，若 Bearer 无效也不得回退到 user_id（应 401）。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "AUTH_ALLOW_LEGACY_USER_ID_ONLY", True)
        response = client.post(
            "/api/recommendations/import-external",
            params={"user_id": sample_user.id},
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "small"},
            headers={"Authorization": "Bearer invalid.token.signature"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后再导入站外视频"

    def test_recommendation_ops_metrics_requires_bearer_token(self, client):
        """运营聚合接口需要登录态，避免匿名读取运营指标。"""
        response = client.get("/api/recommendations/ops/metrics")
        assert response.status_code == 401
        assert response.json()["detail"] == "请先登录后查看推荐运营指标"

    def test_recommendation_ops_metrics_aggregates_import_and_processing(self, client, db, sample_user):
        """运营聚合接口返回推荐导入成功率与处理完成率。"""
        from app.models.video import Video
        from app.models.video import VideoStatus
        from app.services.recommendation_ops_service import record_recommendation_event
        from app.utils.auth_token import build_auth_token

        completed_video = Video(
            user_id=sample_user.id,
            title="bilibili-BV-completed",
            url="https://www.bilibili.com/video/BV-completed",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="处理完成",
        )
        processing_video = Video(
            user_id=sample_user.id,
            title="bilibili-BV-processing",
            url="https://www.bilibili.com/video/BV-processing",
            status=VideoStatus.PROCESSING,
            process_progress=52,
            current_step="语音识别中",
        )
        db.add_all([completed_video, processing_video])
        db.commit()
        db.refresh(completed_video)
        db.refresh(processing_video)

        record_recommendation_event(
            event_type="recommendation_import_external_requested",
            status="started",
            metadata={},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_import_external_completed",
            status="ok",
            metadata={"video_id": completed_video.id},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_import_external_requested",
            status="started",
            metadata={},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_import_external_completed",
            status="ok",
            metadata={"video_id": processing_video.id},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_import_external_requested",
            status="started",
            metadata={},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_import_external_failed",
            status="error",
            metadata={},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_external_materialization_completed",
            status="degraded",
            metadata={"attempted_count": 3, "materialized_count": 2, "failed_count": 1},
            db=db,
        )

        response = client.get(
            "/api/recommendations/ops/metrics",
            params={"days": 7},
            headers={
                "Authorization": f"Bearer {build_auth_token(sample_user.id)}",
                "X-Trace-Id": "ops-trace-001",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("X-Trace-Id") == "ops-trace-001"

        payload = response.json()
        assert payload["message"] == "获取推荐运营指标成功"
        assert payload["data_source"] in {"database", "memory_fallback"}
        assert payload["window_days"] == 7
        assert payload["recommendation_import"]["requested_count"] == 3
        assert payload["recommendation_import"]["completed_count"] == 2
        assert payload["recommendation_import"]["failed_count"] == 1
        assert payload["recommendation_import"]["in_flight_count"] == 0
        assert payload["recommendation_import"]["success_rate"] == pytest.approx(2 / 3, rel=1e-6)
        assert payload["auto_materialization"]["attempted_count"] == 3
        assert payload["auto_materialization"]["materialized_count"] == 2
        assert payload["auto_materialization"]["failed_count"] == 1
        assert payload["auto_materialization"]["success_rate"] == pytest.approx(2 / 3, rel=1e-6)
        assert payload["processing"]["tracked_video_count"] == 2
        assert payload["processing"]["completed_count"] == 1
        assert payload["processing"]["failed_count"] == 0
        assert payload["processing"]["in_progress_count"] == 1
        assert payload["processing"]["completion_rate"] == pytest.approx(0.5, rel=1e-6)
        assert payload["processing"]["status_breakdown"]["completed"] == 1
        assert payload["processing"]["status_breakdown"]["processing"] == 1

    def test_recommendation_ops_metrics_persists_beyond_memory_buffer(self, client, db, sample_user):
        """清空内存缓冲后，聚合接口仍可从数据库恢复口径。"""
        from app.models.video import Video
        from app.models.video import VideoStatus
        from app.services.recommendation_ops_service import record_recommendation_event
        from app.services.recommendation_ops_service import reset_recommendation_event_store_for_tests
        from app.utils.auth_token import build_auth_token

        completed_video = Video(
            user_id=sample_user.id,
            title="persist-check",
            url="https://www.youtube.com/watch?v=persist-check",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            current_step="处理完成",
        )
        db.add(completed_video)
        db.commit()
        db.refresh(completed_video)

        record_recommendation_event(
            event_type="recommendation_import_external_requested",
            status="started",
            trace_id="persist-001",
            metadata={},
            db=db,
        )
        record_recommendation_event(
            event_type="recommendation_import_external_completed",
            status="ok",
            trace_id="persist-001",
            metadata={"video_id": completed_video.id},
            db=db,
        )

        reset_recommendation_event_store_for_tests()

        response = client.get(
            "/api/recommendations/ops/metrics",
            params={"days": 7},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["data_source"] == "database"
        assert payload["recommendation_import"]["requested_count"] == 1
        assert payload["recommendation_import"]["completed_count"] == 1
        assert payload["recommendation_import"]["success_rate"] == pytest.approx(1.0, rel=1e-6)
        assert payload["processing"]["tracked_video_count"] == 1
        assert payload["processing"]["completed_count"] == 1
        assert payload["processing"]["completion_rate"] == pytest.approx(1.0, rel=1e-6)

    def test_recommendation_scenes_emits_telemetry_scene_count(self, client, caplog):
        """GET /scenes 发射 recommendation_scenes_served，metadata.scene_count 与返回条数一致。"""
        caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
        response = client.get("/api/recommendations/scenes", headers={"X-Trace-Id": "trace-scenes-tel"})
        assert response.status_code == 200
        scene_count = len(response.json()["scenes"])
        lines = [r.message for r in caplog.records if r.name == "app.analytics.telemetry"]
        payloads = [json.loads(line) for line in lines]
        served = [p for p in payloads if p.get("event_type") == "recommendation_scenes_served"]
        assert len(served) >= 1
        assert served[0]["module"] == "recommendation"
        assert served[0]["metadata"].get("scene_count") == scene_count

    def test_videos_ranking_telemetry_contract_version_follows_server_setting(self, client, monkeypatch, caplog):
        """ranking 遥测 metadata.contract_version 应与服务端配置一致（不信任中间 payload）。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "RECOMMENDATION_CONTRACT_VERSION", "1", raising=False)
        caplog.set_level(logging.INFO, logger="app.analytics.telemetry")

        response = client.get("/api/recommendations/videos", params={"scene": "home", "limit": 1})
        assert response.status_code == 200
        payload = response.json()
        assert payload["contract_version"] == "1"

        lines = [record.message for record in caplog.records if record.name == "app.analytics.telemetry"]
        telemetry_payloads = [json.loads(line) for line in lines]
        ranking_events = [
            event for event in telemetry_payloads if event.get("event_type") == "recommendation_ranking_completed"
        ]
        assert len(ranking_events) >= 1
        assert ranking_events[0]["metadata"].get("contract_version") == "1"

    def test_import_external_telemetry_url_host_is_hostname(self, client, db, sample_user, monkeypatch, caplog):
        """import-external 遥测 metadata.url_host 为 hostname，非截断 URL。"""
        caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
        monkeypatch.setattr("app.core.executor.submit_task", lambda *args, **kwargs: None)
        from app.utils.auth_token import build_auth_token

        response = client.post(
            "/api/recommendations/import-external",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "model": "small"},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        lines = [r.message for r in caplog.records if r.name == "app.analytics.telemetry"]
        payloads = [json.loads(line) for line in lines]
        requested = [p for p in payloads if p.get("event_type") == "recommendation_import_external_requested"]
        assert len(requested) >= 1
        assert requested[0]["metadata"].get("url_host") == "www.bilibili.com"
