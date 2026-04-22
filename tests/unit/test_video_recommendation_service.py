"""视频推荐服务单测。"""

from app.models.video import Video
from app.models.video import VideoStatus
from app.services import video_recommendation_service as recommendation_service
from app.services.external_candidate_service import ExternalCandidate
from app.services.external_candidate_service import ExternalCandidateFetchReport
from app.services.external_candidate_service import ExternalProviderFetchSummary
from app.services.video_recommendation_service import build_normalized_video_tags
from app.services.video_recommendation_service import build_recommendation_profile
from app.services.video_recommendation_service import recommend_videos
from app.services.video_recommendation_service import sanitize_recommendation_payload_for_client


def fake_fetch_external_candidates_report(*args, **kwargs):
    """返回带 provider 报告的站外候选假数据。"""
    return ExternalCandidateFetchReport(
        candidates=[
            ExternalCandidate(
                id="bilibili:BV1demo",
                provider="bilibili",
                source_label="B站",
                title="B站·导数与函数单调性综合串讲",
                external_url="https://www.bilibili.com/video/BV1demo",
                summary="适合配合站内数学视频继续复盘。",
                tags=["数学", "导数", "函数"],
                subject="数学",
                primary_topic="导数",
                cluster_key="数学::导数",
            )
        ],
        providers=[
            ExternalProviderFetchSummary(
                provider="bilibili",
                source_label="B站",
                status="success",
                candidate_count=1,
                latency_ms=123,
            ),
            ExternalProviderFetchSummary(
                provider="youtube",
                source_label="YouTube",
                status="failed",
                candidate_count=0,
                error_message="timeout",
                latency_ms=800,
            ),
        ],
    )


def fake_fetch_external_candidates_report_with_provider_mix(*args, **kwargs):
    """返回不同 provider 的同主题候选，便于验证来源偏好。"""
    return ExternalCandidateFetchReport(
        candidates=[
            ExternalCandidate(
                id="bilibili:BV1math",
                provider="bilibili",
                source_label="B站",
                title="B站·导数与极限综合串讲",
                external_url="https://www.bilibili.com/video/BV1math",
                summary="同主题数学讲解，但来源不同。",
                tags=["数学", "导数", "极限"],
                subject="数学",
                primary_topic="导数",
                cluster_key="数学::导数",
            ),
            ExternalCandidate(
                id="youtube:math123",
                provider="youtube",
                source_label="YouTube",
                title="YouTube · Derivatives Review",
                external_url="https://www.youtube.com/watch?v=math123",
                summary="与当前微积分主题一致，并保持相同内容来源。",
                tags=["数学", "导数", "微积分"],
                subject="数学",
                primary_topic="导数",
                cluster_key="数学::导数",
            ),
        ],
        providers=[
            ExternalProviderFetchSummary(
                provider="bilibili",
                source_label="B站",
                status="success",
                candidate_count=1,
                latency_ms=120,
            ),
            ExternalProviderFetchSummary(
                provider="youtube",
                source_label="YouTube",
                status="success",
                candidate_count=1,
                latency_ms=140,
            ),
        ],
    )


def test_build_normalized_video_tags_prepends_subject_and_aliases():
    """归一化标签时应补上科目，并收敛常见别名。"""
    video = Video(
        id=1,
        title="高数导数专题",
        filename="math.mp4",
        filepath="/tmp/math.mp4",
        status=VideoStatus.COMPLETED,
        summary="围绕微积分中的导数与极限做系统讲解。",
        tags='["高数","导数","极限"]',
    )

    tags = build_normalized_video_tags(video)

    assert tags[0] == "数学"
    assert "高等数学" in tags
    assert "高数" not in tags


def test_build_recommendation_profile_uses_subject_as_cluster_anchor():
    """推荐画像应包含科目与稳定主题桶。"""
    video = Video(
        id=2,
        title="Python 数据结构入门",
        filename="python.mp4",
        filepath="/tmp/python.mp4",
        status=VideoStatus.COMPLETED,
        summary="介绍栈、队列和链表的核心操作。",
        tags='["Python","数据结构"]',
    )

    profile = build_recommendation_profile(video)

    assert profile.subject == "计算机"
    assert profile.tags[0] == "计算机"
    assert profile.cluster_key.startswith("计算机::")


def test_build_recommendation_profile_marks_combinatorics_as_math():
    """排列组合/插空法类视频也应归入数学科目。"""
    video = Video(
        id=3,
        title="排列组合中的插空法详解",
        filename="combinatorics.mp4",
        filepath="/tmp/combinatorics.mp4",
        status=VideoStatus.COMPLETED,
        summary="讲解排列组合里处理不相邻问题的插空法和空位计数。",
        tags='["插空法","排列组合","空位计数"]',
    )

    profile = build_recommendation_profile(video)

    assert profile.subject == "数学"
    assert profile.tags[0] == "数学"
    assert profile.cluster_key.startswith("数学::")


def test_recommend_videos_related_prefers_same_subject_match():
    """相关推荐在主题重合较弱时，也应优先返回同科目内容。"""
    seed_video = Video(
        id=10,
        title="牛顿第二定律串讲",
        filename="seed.mp4",
        filepath="/tmp/seed.mp4",
        status=VideoStatus.COMPLETED,
        summary="高中物理受力分析与牛顿第二定律综合应用。",
        tags=None,
    )
    same_subject_video = Video(
        id=11,
        title="受力分析复习",
        filename="physics.mp4",
        filepath="/tmp/physics.mp4",
        status=VideoStatus.COMPLETED,
        summary="物理力学里常见的受力分析步骤整理。",
        tags=None,
    )
    other_video = Video(
        id=12,
        title="英语听力技巧",
        filename="english.mp4",
        filepath="/tmp/english.mp4",
        status=VideoStatus.COMPLETED,
        summary="整理英语听力高频信号词和定位方法。",
        tags='["英语听力"]',
    )

    payload = recommend_videos(
        videos=[seed_video, same_subject_video, other_video],
        scene="related",
        limit=2,
        seed_video=seed_video,
    )

    assert payload["items"][0]["id"] == same_subject_video.id
    assert payload["items"][0]["tags"][0] == "物理"
    assert payload["items"][0]["reason_code"] in {"related", "subject"}


def test_recommend_videos_include_external_candidates(monkeypatch):
    """开启 include_external 后应同时返回站内视频与站外候选。"""
    internal_video = Video(
        id=20,
        title="高数导数专题",
        filename="math.mp4",
        filepath="/tmp/math.mp4",
        status=VideoStatus.COMPLETED,
        process_progress=100,
        summary="围绕导数与函数单调性做系统讲解。",
        tags='["导数","函数"]',
    )

    monkeypatch.setattr(
        recommendation_service,
        "fetch_external_candidates_report",
        fake_fetch_external_candidates_report,
    )

    payload = recommend_videos(
        videos=[internal_video],
        scene="home",
        limit=4,
        include_external=True,
    )

    assert payload["contract_version"] == "2"
    assert any(item["id"] == internal_video.id for item in payload["items"])
    assert any(item.get("is_external") is True for item in payload["items"])
    external_item = next(item for item in payload["items"] if item.get("is_external") is True)
    assert external_item["source_label"] == "B站"
    assert external_item["external_url"] == "https://www.bilibili.com/video/BV1demo"
    assert external_item["can_import"] is True
    assert external_item["action_api"] == "/api/recommendations/import-external"
    assert external_item["action_method"] == "POST"
    assert external_item["action_type"] == "import_external_url"
    assert external_item["action_target"].startswith("/upload?mode=url&url=")
    assert payload["internal_item_count"] == 1
    assert payload["external_item_count"] == 1
    assert payload["external_failed_provider_count"] == 1
    assert payload["external_fetch_failed"] is True
    assert payload["external_query"]["subject"] == "数学"
    assert any(item["provider"] == "internal" for item in payload["sources"])
    assert any(item["provider"] == "bilibili" for item in payload["sources"])
    assert any(item["provider"] == "youtube" and item["status"] == "failed" for item in payload["external_providers"])
    assert payload["strategy"] == "video_status_interest_external_v2"


def test_recommend_videos_related_prefers_same_provider_external_candidate(monkeypatch):
    """相关推荐在主题接近时，也应优先保留与 seed 同来源的平台候选。"""
    seed_video = Video(
        id=24,
        title="Derivatives Crash Course",
        filename="derivatives.mp4",
        filepath="/tmp/derivatives.mp4",
        url="https://www.youtube.com/watch?v=seed999",
        status=VideoStatus.COMPLETED,
        summary="Review derivatives and limits for calculus.",
        tags='["导数","极限","微积分"]',
    )

    monkeypatch.setattr(
        recommendation_service,
        "fetch_external_candidates_report",
        fake_fetch_external_candidates_report_with_provider_mix,
    )

    payload = recommend_videos(
        videos=[seed_video],
        scene="related",
        limit=2,
        seed_video=seed_video,
        include_external=True,
    )

    external_items = [item for item in payload["items"] if item.get("is_external") is True]
    assert external_items[0]["provider"] == "youtube"
    assert external_items[0]["reason_code"] == "external_source_match"
    assert payload["external_query"]["preferred_provider"] == "youtube"
    assert payload["external_query"]["preferred_provider_label"] == "YouTube"


def test_recommend_videos_internal_items_include_open_detail_action():
    """站内推荐条目应带详情页跳转动作，便于前端统一处理。"""
    internal_video = Video(
        id=31,
        title="函数极限与导数复盘",
        filename="math-review.mp4",
        filepath="/tmp/math-review.mp4",
        status=VideoStatus.COMPLETED,
        process_progress=100,
        summary="围绕极限、导数定义与常见求导法则做复盘。",
        tags='["导数","极限","函数"]',
    )

    payload = recommend_videos(
        videos=[internal_video],
        scene="home",
        limit=2,
        include_external=False,
    )

    item = payload["items"][0]
    assert item["item_type"] == "video"
    assert item["provider"] == "internal"
    assert item["can_import"] is False
    assert item["action_type"] == "open_video_detail"
    assert item["action_target"] == f"/videos/{internal_video.id}"
    assert payload["internal_item_count"] == 1
    assert payload["external_item_count"] == 0
    assert payload["sources"][0]["source_label"] == "站内视频"


def test_recommend_videos_enforce_window_and_similarity_threshold():
    """推荐接口窗口应收敛到 6~8，并优先保留相似度 >= 0.55 的同主题内容。"""
    math_videos = [
        Video(
            id=100 + idx,
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
            id=200 + idx,
            title=f"英语听力技巧 {idx}",
            filename=f"english-{idx}.mp4",
            filepath=f"/tmp/english-{idx}.mp4",
            status=VideoStatus.COMPLETED,
            summary="整理英语听力高频信号词与定位方法。",
            tags='["英语","听力","语法"]',
        )
        for idx in range(3)
    ]

    payload = recommend_videos(
        videos=[*math_videos, *english_videos],
        scene="home",
        limit=2,
        include_external=False,
        enforce_return_window=True,
    )

    returned_ids = {item["id"] for item in payload["items"] if isinstance(item.get("id"), int)}
    math_ids = {video.id for video in math_videos}
    english_ids = {video.id for video in english_videos}

    assert 6 <= len(payload["items"]) <= 8
    assert returned_ids.issubset(math_ids)
    assert returned_ids.isdisjoint(english_ids)


def test_recommend_videos_backfill_to_min_items_when_similarity_filtered(monkeypatch):
    """当阈值过滤后结果不足时，应回填到最小返回条数（默认 6）。"""
    videos = [
        Video(
            id=300 + idx,
            title=f"数学导数专题回填 {idx}",
            filename=f"backfill-{idx}.mp4",
            filepath=f"/tmp/backfill-{idx}.mp4",
            status=VideoStatus.COMPLETED,
            summary="围绕导数、函数与极限进行复盘。",
            tags='["数学","导数","函数"]',
        )
        for idx in range(8)
    ]

    monkeypatch.setattr(recommendation_service, "compute_internal_similarity", lambda *args, **kwargs: 0.1)

    payload = recommend_videos(
        videos=videos,
        scene="home",
        limit=6,
        include_external=False,
        enforce_return_window=True,
    )

    assert len(payload["items"]) >= 6


def test_sanitize_recommendation_payload_for_client_removes_slice_fields():
    """推荐出口清洗应统一清空 summary/reason_text/tags。"""
    payload = {
        "scene": "home",
        "internal_item_count": 2,
        "external_item_count": 1,
        "items": [
            {
                "id": 1,
                "title": "导数专题",
                "summary": "这是一段摘要",
                "tags": ["数学", "导数"],
                "reason_text": "与当前主题相关",
                "is_external": False,
                "provider": "internal",
                "source_label": "站内视频",
            },
            {
                "id": 2,
                "title": "排列组合插空法详解",
                "summary": "应被过滤",
                "tags": ["数学"],
                "reason_text": "应被过滤",
                "is_external": False,
                "provider": "internal",
                "source_label": "站内视频",
            },
        ],
    }
    sanitized = sanitize_recommendation_payload_for_client(payload)

    assert sanitized["items"][0]["summary"] == ""
    assert sanitized["items"][0]["tags"] == []
    assert sanitized["items"][0]["reason_text"] == ""
    assert all("排列组合插空法详解" not in str(item.get("title") or "") for item in sanitized["items"])
    assert sanitized["internal_item_count"] == 1
    assert sanitized["external_item_count"] == 0
