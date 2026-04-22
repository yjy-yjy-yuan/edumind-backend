"""基于现有视频库与用户资料的轻量推荐服务。"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from typing import Optional
from urllib.parse import urlencode
from urllib.parse import urlparse

from app.core.config import settings
from app.models.user import User
from app.models.video import Video
from app.models.video import VideoStatus
from app.services.external_candidate_service import ExternalCandidate
from app.services.external_candidate_service import ExternalProviderFetchSummary
from app.services.external_candidate_service import fetch_external_candidates_report
from app.services.external_candidate_service import serialize_provider_summary
from app.services.search.similarity_fusion import fused_similarity_score
from app.services.search.similarity_fusion import lexical_overlap_score
from app.services.video_content_service import build_subject_enriched_tags
from app.services.video_content_service import fallback_primary_topic_name
from app.services.video_content_service import infer_subject_from_text
from sqlalchemy.orm import Session

SCENE_OPTIONS = [
    {
        "value": "home",
        "label": "首页推荐",
        "description": "适合首页首屏，优先返回需要继续处理、值得复盘或符合学习方向的视频。",
        "requires_seed": False,
    },
    {
        "value": "continue",
        "label": "继续学习",
        "description": "优先返回处理中、失败待补跑和最近进入的视频，帮助用户回到当前链路。",
        "requires_seed": False,
    },
    {
        "value": "review",
        "label": "复盘推荐",
        "description": "优先返回已完成且摘要/标签较完整的视频，适合整理笔记与复习。",
        "requires_seed": False,
    },
    {
        "value": "related",
        "label": "相似内容",
        "description": "根据 seed 视频的标题、摘要与标签推荐主题相关的视频。",
        "requires_seed": True,
    },
]
SCENE_MAP = {item["value"]: item for item in SCENE_OPTIONS}
ACTIVE_STATUSES = {VideoStatus.PENDING.value, VideoStatus.PROCESSING.value, VideoStatus.DOWNLOADING.value}
TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,24}|[\u4e00-\u9fff]{2,12}")
STOP_TOKENS = {
    "video",
    "lesson",
    "course",
    "study",
    "learning",
    "课程",
    "视频",
    "学习",
    "内容",
    "专题",
    "讲解",
    "复习",
    "基础",
    "入门",
    "精讲",
    "总结",
}
PROVIDER_HOST_KEYWORDS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "youtube": ("youtube.com", "youtu.be"),
    "icourse163": ("icourse163.org",),
}
PROVIDER_SOURCE_LABELS = {
    "bilibili": "B站",
    "youtube": "YouTube",
    "icourse163": "中国大学慕课",
}


@dataclass
class RecommendationProfile:
    """推荐排序使用的视频画像。"""

    subject: str
    tags: list[str]
    primary_topic: str
    tokens: list[str]
    cluster_key: str


@dataclass
class ExternalQueryContext:
    """站外候选检索上下文。"""

    query_text: str
    subject: str
    primary_topic: str
    preferred_tags: list[str]
    preferred_provider: str


@dataclass
class RecommendationQuerySummary:
    """推荐接口返回给前端的站外检索摘要。"""

    query_text: str
    subject: str
    primary_topic: str
    preferred_tags: list[str]
    preferred_provider: str


@dataclass
class RecommendationSourceSummary:
    """推荐结果来源统计。"""

    source_type: str
    provider: str
    source_label: str
    count: int


@dataclass
class RankedExternalRecommendationBundle:
    """站外推荐排序结果与 provider 报告。"""

    items: list[tuple[float, datetime, dict]]
    providers: list[ExternalProviderFetchSummary]


@dataclass
class RecommendationSimilarityContext:
    """推荐相似度计算上下文。"""

    query_text: str
    focus_subject: str
    focus_topic: str
    focus_tokens: set[str]


def list_recommendation_scenes() -> list[dict]:
    """返回支持的推荐场景。"""
    return [dict(item) for item in SCENE_OPTIONS]


def load_candidate_videos_for_recommendation(db: Session, seed_video: Optional[Video], max_scan: int) -> list[Video]:
    """加载推荐用站内候选：按更新时间倒序截断，related 场景保证包含 seed。"""
    if max_scan < 1:
        return []
    order_cols = (Video.updated_at.desc(), Video.upload_time.desc())
    if seed_video is None:
        return db.query(Video).order_by(*order_cols).limit(max_scan).all()
    others = db.query(Video).filter(Video.id != seed_video.id).order_by(*order_cols).limit(max(0, max_scan - 1)).all()
    merged: list[Video] = [seed_video, *others]
    seen: set[int] = set()
    deduped: list[Video] = []
    for video in merged:
        if video.id in seen:
            continue
        seen.add(video.id)
        deduped.append(video)
    return deduped[:max_scan]


def normalize_scene(scene: Optional[str]) -> str:
    """标准化推荐场景。"""
    value = str(scene or "home").strip().lower()
    return value if value in SCENE_MAP else "home"


def parse_video_tags(video: Video) -> list[str]:
    """解析视频标签。"""
    if isinstance(video.tags, list):
        source = video.tags
    else:
        try:
            source = json.loads(video.tags) if video.tags else []
        except (TypeError, ValueError, json.JSONDecodeError):
            source = str(video.tags or "").split(",")

    return [str(item).strip() for item in source if str(item).strip()]


def build_normalized_video_tags(video: Video) -> list[str]:
    """为推荐链路生成带科目的归一化标签。"""
    return build_subject_enriched_tags(
        parse_video_tags(video),
        title=video.title or "",
        summary=video.summary or "",
        max_tags=8,
    )


def build_internal_action_target(video: Video) -> str:
    """生成站内视频详情页目标。"""
    return f"/videos/{video.id}"


def build_external_action_target(candidate: ExternalCandidate) -> str:
    """生成站外候选导入目标。"""
    if not candidate.can_import:
        return candidate.external_url
    query = urlencode(
        {
            "mode": "url",
            "url": candidate.external_url,
            "source": candidate.source_label,
        }
    )
    return f"/upload?{query}"


def unique_tokens(values: Iterable[str]) -> list[str]:
    """提取并去重文本 token。"""
    seen = set()
    tokens = []
    for value in values:
        for raw in TOKEN_RE.findall(str(value or "").lower()):
            token = raw.strip().lower()
            if len(token) < 2 or token in STOP_TOKENS or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def normalize_similarity_threshold() -> float:
    """读取并规范化推荐相似度阈值（0~1）。"""
    raw = float(getattr(settings, "RECOMMENDATION_SIMILARITY_MIN_SCORE", 0.55) or 0.55)
    return max(0.0, min(raw, 1.0))


def normalize_recommendation_limit(limit: int) -> int:
    """将请求 limit 归一到后端推荐窗口（默认 6~8）。"""
    min_items = max(1, int(getattr(settings, "RECOMMENDATION_RETURN_MIN_ITEMS", 6) or 6))
    max_items = max(min_items, int(getattr(settings, "RECOMMENDATION_RETURN_MAX_ITEMS", 8) or 8))
    return max(min_items, min(int(limit or min_items), max_items))


def normalize_recommendation_min_items() -> int:
    """读取推荐最小返回条数。"""
    return max(1, int(getattr(settings, "RECOMMENDATION_RETURN_MIN_ITEMS", 6) or 6))


def _recommendation_item_identity(item: dict) -> str:
    """构建推荐条目标识，用于补齐时去重。"""
    if not isinstance(item, dict):
        return ""
    raw_id = item.get("id")
    if raw_id not in (None, ""):
        return f"id:{raw_id}"
    external_url = str(item.get("external_url") or "").strip()
    if external_url:
        return f"url:{external_url}"
    title = str(item.get("title") or "").strip()
    if title:
        return f"title:{title}"
    return ""


def build_similarity_context(
    *,
    query_context: Optional[ExternalQueryContext],
    seed_profile: Optional[RecommendationProfile],
    user_tokens: list[str],
    user_subject: str,
) -> RecommendationSimilarityContext:
    """构建推荐条目共享的相似度上下文。"""
    query_text = str(query_context.query_text if query_context is not None else "").strip()
    focus_subject = str(query_context.subject if query_context is not None else "").strip()
    focus_topic = str(query_context.primary_topic if query_context is not None else "").strip()
    seed_tokens = list(seed_profile.tokens) if seed_profile is not None else []
    focus_tokens = set(unique_tokens([query_text, focus_subject, focus_topic, *user_tokens, *seed_tokens]))
    if not query_text:
        query_text = " ".join(part for part in [focus_subject, focus_topic] if part).strip() or "学习内容"
    if not focus_subject:
        focus_subject = user_subject
    return RecommendationSimilarityContext(
        query_text=query_text,
        focus_subject=focus_subject,
        focus_topic=focus_topic,
        focus_tokens=focus_tokens,
    )


def compute_internal_similarity(
    *,
    video: Video,
    profile: RecommendationProfile,
    context: RecommendationSimilarityContext,
) -> float:
    """计算站内推荐候选与当前焦点的融合相似度（0~1）。"""
    candidate_tokens = set(profile.tokens)
    if context.focus_tokens:
        token_overlap = len(candidate_tokens & context.focus_tokens) / max(1, len(context.focus_tokens))
    else:
        token_overlap = lexical_overlap_score(context.query_text, " ".join(profile.tokens))
    subject_match = 1.0 if context.focus_subject and profile.subject == context.focus_subject else 0.0
    topic_match = 1.0 if context.focus_topic and profile.primary_topic == context.focus_topic else 0.0
    semantic_signal = min(1.0, token_overlap * 0.55 + subject_match * 0.35 + topic_match * 0.25)
    fused = fused_similarity_score(
        vector_similarity=semantic_signal,
        query=context.query_text,
        preview_text=" ".join([str(video.summary or ""), *profile.tags]),
        video_title=video.title,
    )
    if subject_match and (topic_match > 0 or token_overlap >= 0.2):
        return max(fused, 0.58)
    return fused


def compute_external_similarity(
    *,
    candidate: ExternalCandidate,
    context: RecommendationSimilarityContext,
) -> float:
    """计算站外候选与当前焦点的融合相似度（0~1）。"""
    candidate_tokens = set(unique_tokens([candidate.title, candidate.summary, candidate.author, *candidate.tags]))
    if context.focus_tokens:
        token_overlap = len(candidate_tokens & context.focus_tokens) / max(1, len(context.focus_tokens))
    else:
        token_overlap = lexical_overlap_score(context.query_text, " ".join(candidate.tags))
    subject_match = 1.0 if context.focus_subject and candidate.subject == context.focus_subject else 0.0
    topic_match = 1.0 if context.focus_topic and candidate.primary_topic == context.focus_topic else 0.0
    semantic_signal = min(1.0, token_overlap * 0.55 + subject_match * 0.35 + topic_match * 0.25)
    fused = fused_similarity_score(
        vector_similarity=semantic_signal,
        query=context.query_text,
        preview_text=" ".join([str(candidate.summary or ""), *candidate.tags]),
        video_title=candidate.title,
    )
    if subject_match and (topic_match > 0 or token_overlap >= 0.2):
        return max(fused, 0.58)
    return fused


def extract_video_tokens(video: Optional[Video]) -> list[str]:
    """提取视频主题 token。"""
    if video is None:
        return []
    return unique_tokens([video.title or "", video.summary or "", *build_normalized_video_tags(video)])


def extract_user_interest_tokens(user: Optional[User]) -> list[str]:
    """提取用户学习兴趣 token。"""
    if user is None:
        return []
    return unique_tokens(
        [
            user.learning_direction or "",
            user.occupation or "",
            user.bio or "",
            user.username or "",
        ]
    )


def extract_user_subject(user: Optional[User]) -> str:
    """提取用户最可能对应的学习科目。"""
    if user is None:
        return ""
    return infer_subject_from_text(
        tags=[],
        title=f"{user.learning_direction or ''} {user.occupation or ''}",
        summary=f"{user.bio or ''} {user.username or ''}",
    )


def infer_preferred_provider_from_url(url: Optional[str]) -> str:
    """从原始视频链接推断更贴近当前内容语境的站外平台。"""
    parsed = urlparse(str(url or "").strip())
    hostname = parsed.netloc.lower()
    if not hostname:
        return ""
    for provider, host_keywords in PROVIDER_HOST_KEYWORDS.items():
        if any(keyword in hostname for keyword in host_keywords):
            return provider
    return ""


def build_recommendation_profile(video: Video) -> RecommendationProfile:
    """构建推荐排序使用的视频画像。"""
    normalized_tags = build_normalized_video_tags(video)
    subject = infer_subject_from_text(tags=normalized_tags, title=video.title or "", summary=video.summary or "")
    primary_topic = fallback_primary_topic_name(
        video.summary or "",
        tags=normalized_tags,
        title=video.title or "",
        max_length=24,
    )
    cluster_basis = primary_topic or (normalized_tags[1] if len(normalized_tags) > 1 else subject)
    cluster_key = "::".join([part for part in [subject, cluster_basis] if part])
    tokens = unique_tokens([video.title or "", video.summary or "", primary_topic, *normalized_tags])
    return RecommendationProfile(
        subject=subject,
        tags=normalized_tags,
        primary_topic=primary_topic,
        tokens=tokens,
        cluster_key=cluster_key,
    )


def recency_bonus(video: Video, now: datetime) -> float:
    """最近活跃视频加分。"""
    anchor = video.updated_at or video.upload_time or now
    hours = max((now - anchor).total_seconds() / 3600, 0)
    if hours <= 24:
        return 18 - min(hours / 2, 12)
    if hours <= 7 * 24:
        return 8 - min((hours - 24) / 24, 6)
    return 0


def _seed_reference_for_reason_text(
    seed_video: Optional[Video],
    *,
    include_seed_title_in_reason: bool,
) -> str:
    """Related 文案中的种子指代；Contract v2 起不拼接种子视频标题（与顶层 seed_video_title 下线口径一致）。"""
    if seed_video is None:
        return "当前种子视频"
    if include_seed_title_in_reason:
        return f"《{seed_video.title or '当前视频'}》"
    return "当前种子视频"


def build_reason(
    *,
    scene: str,
    status: str,
    seed_video: Optional[Video],
    subject: str,
    same_subject: bool,
    cluster_size: int,
    seed_overlap: list[str],
    interest_overlap: list[str],
    has_summary: bool,
    has_tags: bool,
    include_seed_title_in_reason: bool = True,
) -> tuple[str, str, str]:
    """构建推荐理由。"""
    if status in ACTIVE_STATUSES and scene in {"home", "continue"}:
        return ("continue", "继续跟进", "当前任务仍在处理中，适合回到详情页继续跟进状态。")

    if status == VideoStatus.FAILED.value and scene in {"home", "continue"}:
        return ("retry", "建议补跑", "该视频上次处理失败，适合优先回到详情页重新提交。")

    seed_ref = _seed_reference_for_reason_text(seed_video, include_seed_title_in_reason=include_seed_title_in_reason)

    if scene == "related" and seed_video is not None and seed_overlap:
        topic_text = "、".join(seed_overlap[:2])
        return ("related", "主题相关", f"与{seed_ref}共享 {topic_text} 等主题。")

    if scene == "related" and seed_video is not None and same_subject and subject:
        return ("subject", "同科目", f"与{seed_ref}同属 {subject}，适合继续串联学习。")

    if interest_overlap:
        topic_text = "、".join(interest_overlap[:2])
        return ("interest", "匹配方向", f"内容与你当前学习方向里的 {topic_text} 更相关。")

    if scene in {"home", "review"} and subject and cluster_size >= 2:
        return ("cluster", "同科聚类", f"已归入 {subject} 主题，视频库里还有 {cluster_size - 1} 条相关内容。")

    if scene == "review" and has_summary:
        return ("review", "适合复盘", "摘要已就绪，适合继续整理笔记与回看重点。")

    if has_tags:
        return ("tagged", "重点清晰", "已提炼标签，适合进一步问答或归纳。")

    return ("recent", "最近内容", "最近进入视频库，适合从这里继续学习。")


def build_external_reason(
    *,
    scene: str,
    candidate: ExternalCandidate,
    seed_video: Optional[Video],
    same_subject: bool,
    seed_overlap: list[str],
    interest_overlap: list[str],
    same_provider: bool,
    include_seed_title_in_reason: bool = True,
) -> tuple[str, str, str]:
    """构建站外候选推荐理由。"""
    seed_ref = _seed_reference_for_reason_text(seed_video, include_seed_title_in_reason=include_seed_title_in_reason)

    if scene == "related" and seed_video is not None and same_provider:
        return (
            "external_source_match",
            "同来源延伸",
            f"与{seed_ref}来自同一内容来源，延续当前频道语境更自然。",
        )

    if scene == "related" and seed_video is not None and seed_overlap:
        topic_text = "、".join(seed_overlap[:2])
        return (
            "external_related",
            "站外同主题",
            f"与{seed_ref}共享 {topic_text} 等主题，可直接导入继续学习。",
        )

    if scene == "related" and seed_video is not None and same_subject and candidate.subject:
        return (
            "external_subject",
            "站外同科",
            f"与{seed_ref}同属 {candidate.subject}，适合站内外串联学习。",
        )

    if scene == "review":
        return ("external_review", "站外复盘", "适合配合当前已完成内容继续做复盘或横向补充。")

    if scene == "continue":
        return ("external_continue", "站外延伸", "当前场景可继续从站外课程扩展同主题内容，并随时导回学习链路。")

    if interest_overlap:
        topic_text = "、".join(interest_overlap[:2])
        return ("external_interest", "站外匹配", f"这条站外候选与你当前学习方向里的 {topic_text} 更相关。")

    return ("external_discovery", "站外发现", "把站内学习主题继续扩展到 B站、YouTube 或慕课内容。")


def score_video(
    *,
    video: Video,
    scene: str,
    now: datetime,
    seed_video: Optional[Video],
    user_tokens: list[str],
    user_subject: str,
    profile: RecommendationProfile,
    seed_profile: Optional[RecommendationProfile],
    cluster_size: int,
    subject_cluster_size: int,
    include_seed_title_in_reason: bool = True,
) -> tuple[float, str, str, str]:
    """对单个视频打分并返回理由。"""
    status = video.status.value if isinstance(video.status, VideoStatus) else str(video.status or "").lower()
    tags = profile.tags
    video_tokens = profile.tokens
    interest_overlap = [token for token in user_tokens if token in video_tokens]
    seed_overlap = []
    same_subject = False
    if seed_profile is not None:
        seed_tokens = set(seed_profile.tokens)
        seed_overlap = [token for token in video_tokens if token in seed_tokens]
        same_subject = bool(profile.subject and profile.subject == seed_profile.subject)

    score = recency_bonus(video, now)
    has_summary = bool(str(video.summary or "").strip())
    has_tags = bool(tags)

    if scene in {"home", "continue"}:
        if status in ACTIVE_STATUSES:
            score += 120 - min(float(video.process_progress or 0) / 5, 18)
        elif status == VideoStatus.FAILED.value:
            score += 96
        elif status == VideoStatus.COMPLETED.value:
            score += 64
        else:
            score += 40
    elif scene == "review":
        if status == VideoStatus.COMPLETED.value:
            score += 112
        elif status in ACTIVE_STATUSES:
            score += 28
        else:
            score += 12
    elif scene == "related":
        score += len(seed_overlap) * 30
        if same_subject:
            score += 36
        if profile.primary_topic and seed_profile is not None and profile.primary_topic == seed_profile.primary_topic:
            score += 28
        if status == VideoStatus.COMPLETED.value:
            score += 44
        elif status in ACTIVE_STATUSES:
            score += 18
        else:
            score += 10

    if has_summary:
        score += 12
    if has_tags:
        score += min(len(tags), 4) * 5
    if profile.subject:
        score += 10
    if cluster_size >= 2:
        score += min((cluster_size - 1) * 7, 21)
    if subject_cluster_size >= 2:
        score += min((subject_cluster_size - 1) * 4, 12)
    if user_subject and profile.subject and user_subject == profile.subject:
        score += 24
    score += len(interest_overlap) * 16

    reason_code, reason_label, reason_text = build_reason(
        scene=scene,
        status=status,
        seed_video=seed_video,
        subject=profile.subject,
        same_subject=same_subject,
        cluster_size=cluster_size,
        seed_overlap=seed_overlap,
        interest_overlap=interest_overlap,
        has_summary=has_summary,
        has_tags=has_tags,
        include_seed_title_in_reason=include_seed_title_in_reason,
    )
    return score, reason_code, reason_label, reason_text


def score_external_candidate(
    *,
    candidate: ExternalCandidate,
    scene: str,
    seed_video: Optional[Video],
    seed_profile: Optional[RecommendationProfile],
    user_tokens: list[str],
    user_subject: str,
    focus_subject: str,
    focus_tags: list[str],
    preferred_provider: str,
    include_seed_title_in_reason: bool = True,
) -> tuple[float, str, str, str]:
    """对站外候选打分。"""
    candidate_tokens = unique_tokens([candidate.title, candidate.summary, candidate.author, *candidate.tags])
    interest_overlap = [token for token in user_tokens if token in candidate_tokens]
    seed_overlap = []
    same_subject = False
    same_provider = bool(preferred_provider and candidate.provider == preferred_provider)
    if seed_profile is not None:
        seed_tokens = set(seed_profile.tokens)
        seed_overlap = [token for token in candidate_tokens if token in seed_tokens]
        same_subject = bool(candidate.subject and candidate.subject == seed_profile.subject)
    elif focus_subject and candidate.subject:
        same_subject = candidate.subject == focus_subject

    tag_overlap = [tag for tag in candidate.tags if tag in set(focus_tags)]
    score = 52
    if scene == "related":
        score = 68
        score += len(seed_overlap) * 18
        if same_subject:
            score += 20
        if same_provider:
            score += 28
        elif preferred_provider:
            score -= 8
    elif scene == "review":
        score = 62
        if same_provider:
            score += 8
    elif scene == "continue":
        score = 58
        if same_provider:
            score += 10

    score += len(tag_overlap) * 10
    score += len(interest_overlap) * 12
    if candidate.subject and candidate.subject == user_subject:
        score += 16
    elif candidate.subject and candidate.subject == focus_subject:
        score += 10
    if candidate.primary_topic and seed_profile is not None and candidate.primary_topic == seed_profile.primary_topic:
        score += 14
    if candidate.upload_time is not None:
        score += 4

    reason_code, reason_label, reason_text = build_external_reason(
        scene=scene,
        candidate=candidate,
        seed_video=seed_video,
        same_subject=same_subject,
        seed_overlap=seed_overlap,
        interest_overlap=interest_overlap,
        same_provider=same_provider,
        include_seed_title_in_reason=include_seed_title_in_reason,
    )
    return score, reason_code, reason_label, reason_text


def serialize_recommendation_item(
    video: Video,
    *,
    profile: RecommendationProfile,
    score: float,
    reason_code: str,
    reason_label: str,
    reason_text: str,
) -> dict:
    """序列化推荐视频。"""
    status = video.status.value if isinstance(video.status, VideoStatus) else str(video.status or "")
    processing_origin = (
        video.processing_origin.value if hasattr(video.processing_origin, "value") else video.processing_origin
    )
    return {
        "id": video.id,
        "title": video.title,
        "status": status,
        "upload_time": video.upload_time,
        "summary": video.summary,
        "tags": profile.tags,
        "process_progress": float(video.process_progress or 0),
        "current_step": str(video.current_step or ""),
        "processing_origin": processing_origin,
        "processing_origin_label": "iOS 离线处理" if str(processing_origin) == "ios_offline" else "在线处理",
        "upload_source": video.get_upload_source(),
        "upload_source_label": video.get_upload_source_label(),
        "recommendation_score": round(score, 2),
        "reason_code": reason_code,
        "reason_label": reason_label,
        "reason_text": reason_text,
        "is_external": False,
        "item_type": "video",
        "source_label": "站内视频",
        "external_source_label": None,
        "external_url": None,
        "subject": profile.subject,
        "cluster_key": profile.cluster_key,
        "author": None,
        "provider": "internal",
        "can_import": False,
        "import_hint": "",
        "action_type": "open_video_detail",
        "action_label": "打开详情",
        "action_target": build_internal_action_target(video),
        "action_api": None,
        "action_method": None,
    }


def serialize_external_candidate_item(
    candidate: ExternalCandidate,
    *,
    score: float,
    reason_code: str,
    reason_label: str,
    reason_text: str,
) -> dict:
    """序列化站外候选条目。"""
    action_type = "import_external_url" if candidate.can_import else "open_external_source"
    action_label = "导入学习" if candidate.can_import else "打开来源"
    return {
        "id": candidate.id,
        "title": candidate.title,
        "status": "",
        "upload_time": candidate.upload_time,
        "summary": candidate.summary,
        "tags": candidate.tags,
        "process_progress": 0,
        "current_step": "",
        "processing_origin": "external_candidate",
        "processing_origin_label": "站外候选",
        "upload_source": "external_candidate",
        "upload_source_label": candidate.source_label,
        "recommendation_score": round(score, 2),
        "reason_code": reason_code,
        "reason_label": reason_label,
        "reason_text": reason_text,
        "is_external": True,
        "item_type": "external_candidate",
        "source_label": candidate.source_label,
        "external_source_label": candidate.source_label,
        "external_url": candidate.external_url,
        "subject": candidate.subject,
        "cluster_key": candidate.cluster_key,
        "author": candidate.author,
        "provider": candidate.provider,
        "can_import": candidate.can_import,
        "import_hint": candidate.import_hint,
        "action_type": action_type,
        "action_label": action_label,
        "action_target": build_external_action_target(candidate),
        "action_api": "/api/recommendations/import-external" if candidate.can_import else None,
        "action_method": "POST" if candidate.can_import else None,
    }


def build_external_query_context(
    *,
    videos: list[Video],
    profiles: dict[int, RecommendationProfile],
    scene: str,
    seed_video: Optional[Video],
    seed_profile: Optional[RecommendationProfile],
    user_subject: str,
) -> ExternalQueryContext:
    """为站外候选推导搜索上下文。"""
    if seed_video is not None and seed_profile is not None:
        preferred_provider = infer_preferred_provider_from_url(seed_video.url)
        query_text = " ".join(part for part in [seed_profile.subject, seed_profile.primary_topic] if part).strip()
        return ExternalQueryContext(
            query_text=query_text or (seed_video.title or "学习课程"),
            subject=seed_profile.subject,
            primary_topic=seed_profile.primary_topic,
            preferred_tags=seed_profile.tags[:4],
            preferred_provider=preferred_provider,
        )

    ordered_profiles = [profiles[video.id] for video in videos if video.id in profiles]
    subject_counter = Counter(profile.subject for profile in ordered_profiles if profile.subject)
    focus_subject = user_subject or (subject_counter.most_common(1)[0][0] if subject_counter else "")
    subject_profiles = [
        profile for profile in ordered_profiles if not focus_subject or profile.subject == focus_subject
    ]
    if not subject_profiles:
        subject_profiles = ordered_profiles

    topic_counter = Counter(profile.primary_topic for profile in subject_profiles if profile.primary_topic)
    primary_topic = topic_counter.most_common(1)[0][0] if topic_counter else ""
    tag_counter = Counter(tag for profile in subject_profiles for tag in profile.tags[1:5])
    preferred_tags = [tag for tag, _ in tag_counter.most_common(4)]
    query_text = " ".join(part for part in [focus_subject, primary_topic] if part).strip()
    if not query_text and preferred_tags:
        query_text = " ".join(preferred_tags[:2])
    if not query_text:
        fallback_title = next((video.title for video in videos if str(video.title or "").strip()), "")
        query_text = fallback_title or "学习课程"
    if scene == "review" and focus_subject and focus_subject not in preferred_tags:
        preferred_tags = [focus_subject, *preferred_tags]

    return ExternalQueryContext(
        query_text=query_text,
        subject=focus_subject,
        primary_topic=primary_topic,
        preferred_tags=preferred_tags,
        preferred_provider="",
    )


def serialize_query_summary(query_context: ExternalQueryContext) -> dict:
    """序列化站外检索摘要。"""
    summary = RecommendationQuerySummary(
        query_text=query_context.query_text,
        subject=query_context.subject,
        primary_topic=query_context.primary_topic,
        preferred_tags=list(query_context.preferred_tags),
        preferred_provider=query_context.preferred_provider,
    )
    return {
        "query_text": summary.query_text,
        "subject": summary.subject,
        "primary_topic": summary.primary_topic,
        "preferred_tags": summary.preferred_tags,
        "preferred_provider": summary.preferred_provider,
        "preferred_provider_label": PROVIDER_SOURCE_LABELS.get(summary.preferred_provider, ""),
    }


def summarize_recommendation_sources(items: list[dict]) -> list[dict]:
    """汇总推荐结果来源分布。"""
    counter = Counter()
    labels = {}
    source_types = {}

    for item in items:
        is_external = bool(item.get("is_external"))
        provider = str(item.get("provider") or ("external" if is_external else "internal")).strip()
        source_label = str(item.get("source_label") or ("站外候选" if is_external else "站内视频")).strip()
        source_type = "external" if is_external else "internal"
        key = (source_type, provider, source_label)
        counter[key] += 1
        labels[key] = source_label
        source_types[key] = source_type

    ordered_keys = sorted(
        counter.keys(),
        key=lambda item: (
            0 if source_types[item] == "internal" else 1,
            -counter[item],
            labels[item],
        ),
    )
    summaries = []
    for source_type, provider, source_label in ordered_keys:
        summary = RecommendationSourceSummary(
            source_type=source_type,
            provider=provider,
            source_label=source_label,
            count=counter[(source_type, provider, source_label)],
        )
        summaries.append(
            {
                "source_type": summary.source_type,
                "provider": summary.provider,
                "source_label": summary.source_label,
                "count": summary.count,
            }
        )
    return summaries


def count_external_items(items: list[dict]) -> int:
    """统计返回结果中的站外候选数量。"""
    return sum(1 for item in items if item.get("is_external"))


def count_internal_items(items: list[dict]) -> int:
    """统计返回结果中的站内条目数量。"""
    return sum(1 for item in items if not item.get("is_external"))


def _normalize_excluded_title_keywords() -> list[str]:
    """读取推荐结果标题黑名单关键词。"""
    raw = str(getattr(settings, "RECOMMENDATION_EXCLUDED_TITLE_KEYWORDS", "") or "").strip()
    keywords = [part.strip() for part in raw.split(",") if part.strip()]
    # 兜底保留强制拦截词，避免配置缺失时回归。
    if "排列组合插空法详解" not in keywords:
        keywords.append("排列组合插空法详解")
    return keywords


def _is_excluded_recommendation_title(title: str, keywords: list[str]) -> bool:
    """判断推荐标题是否命中黑名单关键词。"""
    text = str(title or "").strip()
    if not text:
        return False
    lowered = text.lower()
    for keyword in keywords:
        key = str(keyword or "").strip()
        if not key:
            continue
        if key in text or key.lower() in lowered:
            return True
    return False


def sanitize_recommendation_items_for_client(items: list[dict]) -> list[dict]:
    """前端展示口径收口：推荐条目不暴露切片化文本与标签。"""
    sanitized: list[dict] = []
    excluded_keywords = _normalize_excluded_title_keywords()
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if _is_excluded_recommendation_title(row.get("title"), excluded_keywords):
            continue
        row["summary"] = ""
        row["tags"] = []
        row["reason_text"] = ""
        sanitized.append(row)
    return sanitized


def sanitize_recommendation_payload_for_client(payload: dict) -> dict:
    """统一清洗推荐响应 payload，保证多出口一致。"""
    merged = dict(payload or {})
    items = sanitize_recommendation_items_for_client(list(merged.get("items") or []))
    merged["items"] = items
    merged["internal_item_count"] = count_internal_items(items)
    merged["external_item_count"] = count_external_items(items)
    merged["sources"] = summarize_recommendation_sources(items)
    return merged


def summarize_external_providers(provider_summaries: list[ExternalProviderFetchSummary]) -> list[dict]:
    """序列化站外 provider 抓取摘要。"""
    return [serialize_provider_summary(summary) for summary in provider_summaries]


def count_failed_external_providers(provider_summaries: list[ExternalProviderFetchSummary]) -> int:
    """统计失败的站外 provider 数量。"""
    return sum(1 for summary in provider_summaries if summary.status == "failed")


def build_ranked_internal_items(
    *,
    videos: list[Video],
    scene: str,
    now: datetime,
    seed_video: Optional[Video],
    user_tokens: list[str],
    user_subject: str,
    profiles: dict[int, RecommendationProfile],
    exclude_ids: set[int],
    similarity_context: RecommendationSimilarityContext,
    similarity_threshold: float,
    include_seed_title_in_reason: bool = True,
) -> list[tuple[float, datetime, dict]]:
    """生成站内推荐条目。"""
    scored_items = []
    cluster_counter = Counter(profile.cluster_key for profile in profiles.values() if profile.cluster_key)
    subject_counter = Counter(profile.subject for profile in profiles.values() if profile.subject)
    seed_profile = profiles.get(seed_video.id) if seed_video is not None else None

    for video in videos:
        if video.id in exclude_ids:
            continue
        if seed_video is not None and video.id == seed_video.id:
            continue
        profile = profiles.get(video.id)
        if profile is None:
            continue
        similarity = compute_internal_similarity(video=video, profile=profile, context=similarity_context)
        if similarity < similarity_threshold:
            continue

        score, reason_code, reason_label, reason_text = score_video(
            video=video,
            scene=scene,
            now=now,
            seed_video=seed_video,
            user_tokens=user_tokens,
            user_subject=user_subject,
            profile=profile,
            seed_profile=seed_profile,
            cluster_size=cluster_counter.get(profile.cluster_key, 0),
            subject_cluster_size=subject_counter.get(profile.subject, 0),
            include_seed_title_in_reason=include_seed_title_in_reason,
        )
        scored_items.append(
            (
                score,
                video.updated_at or video.upload_time or datetime.min,
                serialize_recommendation_item(
                    video,
                    profile=profile,
                    score=score,
                    reason_code=reason_code,
                    reason_label=reason_label,
                    reason_text=reason_text,
                ),
            )
        )

    scored_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored_items


def build_ranked_external_items(
    *,
    query_context: ExternalQueryContext,
    scene: str,
    seed_video: Optional[Video],
    seed_profile: Optional[RecommendationProfile],
    user_tokens: list[str],
    user_subject: str,
    limit: int,
    similarity_context: RecommendationSimilarityContext,
    similarity_threshold: float,
    include_seed_title_in_reason: bool = True,
) -> RankedExternalRecommendationBundle:
    """生成站外候选条目，并附带 provider 报告。"""
    report = fetch_external_candidates_report(
        query_context.query_text,
        subject_hint=query_context.subject,
        preferred_tags=query_context.preferred_tags,
        preferred_provider=query_context.preferred_provider,
        limit=max(1, limit),
    )
    ranked_items = []
    for candidate in report.candidates:
        similarity = compute_external_similarity(candidate=candidate, context=similarity_context)
        if similarity < similarity_threshold:
            continue
        score, reason_code, reason_label, reason_text = score_external_candidate(
            candidate=candidate,
            scene=scene,
            seed_video=seed_video,
            seed_profile=seed_profile,
            user_tokens=user_tokens,
            user_subject=user_subject,
            focus_subject=query_context.subject,
            focus_tags=query_context.preferred_tags,
            preferred_provider=query_context.preferred_provider,
            include_seed_title_in_reason=include_seed_title_in_reason,
        )
        ranked_items.append(
            (
                score,
                candidate.upload_time or datetime.min,
                serialize_external_candidate_item(
                    candidate,
                    score=score,
                    reason_code=reason_code,
                    reason_label=reason_label,
                    reason_text=reason_text,
                ),
            )
        )
    ranked_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return RankedExternalRecommendationBundle(items=ranked_items, providers=report.providers)


def select_combined_items(
    *,
    internal_items: list[tuple[float, datetime, dict]],
    external_items: list[tuple[float, datetime, dict]],
    limit: int,
) -> list[dict]:
    """按总条数挑选站内/站外混合结果，尽量保留双边入口。"""
    if limit <= 1:
        combined = sorted(internal_items + external_items, key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in combined[:limit]]
    if not external_items:
        return [item[2] for item in internal_items[:limit]]
    if not internal_items:
        return [item[2] for item in external_items[:limit]]

    external_target = min(2 if limit >= 6 else 1, len(external_items))
    internal_target = min(max(limit - external_target, 1), len(internal_items))

    selected = internal_items[:internal_target] + external_items[:external_target]
    internal_cursor = internal_target
    external_cursor = external_target
    while len(selected) < limit:
        next_internal = internal_items[internal_cursor] if internal_cursor < len(internal_items) else None
        next_external = external_items[external_cursor] if external_cursor < len(external_items) else None
        if next_internal is None and next_external is None:
            break
        if next_external is not None and (
            next_internal is None or (next_external[0], next_external[1]) > (next_internal[0], next_internal[1])
        ):
            selected.append(next_external)
            external_cursor += 1
        else:
            selected.append(next_internal)
            internal_cursor += 1

    selected.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in selected[:limit]]


def build_template_coach_summary(
    *,
    scene: str,
    item_count: int,
    include_external: bool,
    external_fetch_failed: bool,
    external_failed_provider_count: int,
) -> str:
    """单次模板文案，不调用 LLM；用于可选 coach 字段。"""
    if not include_external:
        return f"当前为「{scene}」场景，已基于站内视频库生成 {item_count} 条推荐。" "可在推荐页开启站外检索以扩展来源。"
    if external_fetch_failed:
        return (
            f"站外检索中有 {external_failed_provider_count} 个来源未成功返回，"
            "站内推荐仍可使用。请检查网络或稍后再试；也可在首页关闭站外以加快加载。"
        )
    return f"本轮「{scene}」推荐含站外增强，共 {item_count} 条。" "若加载偏慢，可将站外检索留到推荐页单独刷新。"


def recommend_videos(
    *,
    videos: list[Video],
    scene: str,
    limit: int,
    seed_video: Optional[Video] = None,
    user: Optional[User] = None,
    exclude_ids: Optional[set[int]] = None,
    include_external: bool = False,
    coach: bool = False,
    enforce_return_window: bool = False,
) -> dict:
    """生成推荐视频列表。"""
    normalized_scene = normalize_scene(scene)
    contract_version = str(getattr(settings, "RECOMMENDATION_CONTRACT_VERSION", "2") or "2").strip()
    include_seed_title_in_reason = contract_version == "1"
    effective_limit = normalize_recommendation_limit(limit) if enforce_return_window else max(1, int(limit or 1))
    target_min_items = min(normalize_recommendation_min_items(), effective_limit) if enforce_return_window else 1
    similarity_threshold = normalize_similarity_threshold()
    exclude_ids = set(exclude_ids or set())
    user_tokens = extract_user_interest_tokens(user)
    user_subject = extract_user_subject(user)
    now = datetime.utcnow()
    excluded_title_keywords = _normalize_excluded_title_keywords()
    candidate_videos = [
        video
        for video in list(videos or [])
        if not _is_excluded_recommendation_title(video.title, excluded_title_keywords)
    ]
    profiles = {video.id: build_recommendation_profile(video) for video in candidate_videos}
    seed_profile = profiles.get(seed_video.id) if seed_video is not None else None
    external_query_summary = None
    external_provider_summaries: list[ExternalProviderFetchSummary] = []

    query_context = build_external_query_context(
        videos=candidate_videos,
        profiles=profiles,
        scene=normalized_scene,
        seed_video=seed_video,
        seed_profile=seed_profile,
        user_subject=user_subject,
    )
    similarity_context = build_similarity_context(
        query_context=query_context,
        seed_profile=seed_profile,
        user_tokens=user_tokens,
        user_subject=user_subject,
    )
    internal_ranked_items = build_ranked_internal_items(
        videos=candidate_videos,
        scene=normalized_scene,
        now=now,
        seed_video=seed_video,
        user_tokens=user_tokens,
        user_subject=user_subject,
        profiles=profiles,
        exclude_ids=exclude_ids,
        similarity_context=similarity_context,
        similarity_threshold=similarity_threshold,
        include_seed_title_in_reason=include_seed_title_in_reason,
    )
    items = [item[2] for item in internal_ranked_items[:effective_limit]]

    if include_external:
        external_query_summary = serialize_query_summary(query_context)
        external_bundle = build_ranked_external_items(
            query_context=query_context,
            scene=normalized_scene,
            seed_video=seed_video,
            seed_profile=seed_profile,
            user_tokens=user_tokens,
            user_subject=user_subject,
            limit=min(effective_limit, 4),
            similarity_context=similarity_context,
            similarity_threshold=similarity_threshold,
            include_seed_title_in_reason=include_seed_title_in_reason,
        )
        external_provider_summaries = external_bundle.providers
        items = select_combined_items(
            internal_items=internal_ranked_items,
            external_items=external_bundle.items,
            limit=effective_limit,
        )

    if enforce_return_window and len(items) < target_min_items:
        relaxed_internal_items = build_ranked_internal_items(
            videos=candidate_videos,
            scene=normalized_scene,
            now=now,
            seed_video=seed_video,
            user_tokens=user_tokens,
            user_subject=user_subject,
            profiles=profiles,
            exclude_ids=exclude_ids,
            similarity_context=similarity_context,
            similarity_threshold=0.0,
            include_seed_title_in_reason=include_seed_title_in_reason,
        )
        relaxed_external_items: list[tuple[float, datetime, dict]] = []
        if include_external:
            relaxed_external_items = build_ranked_external_items(
                query_context=query_context,
                scene=normalized_scene,
                seed_video=seed_video,
                seed_profile=seed_profile,
                user_tokens=user_tokens,
                user_subject=user_subject,
                limit=max(effective_limit, target_min_items),
                similarity_context=similarity_context,
                similarity_threshold=0.0,
                include_seed_title_in_reason=include_seed_title_in_reason,
            ).items

        relaxed_candidates = select_combined_items(
            internal_items=relaxed_internal_items,
            external_items=relaxed_external_items,
            limit=effective_limit,
        )
        seen_identities = {_recommendation_item_identity(item) for item in items}
        for candidate in relaxed_candidates:
            identity = _recommendation_item_identity(candidate)
            if not identity or identity in seen_identities:
                continue
            items.append(candidate)
            seen_identities.add(identity)
            if len(items) >= target_min_items:
                break

    fallback_used = False
    if not items and candidate_videos:
        fallback_used = True
        recent_videos = []
        for video in sorted(
            (
                video
                for video in candidate_videos
                if video.id not in exclude_ids and (seed_video is None or video.id != seed_video.id)
            ),
            key=lambda item: item.updated_at or item.upload_time or datetime.min,
            reverse=True,
        ):
            profile = profiles.get(video.id) or build_recommendation_profile(video)
            similarity = compute_internal_similarity(video=video, profile=profile, context=similarity_context)
            if similarity < similarity_threshold:
                continue
            recent_videos.append(video)
            if len(recent_videos) >= effective_limit:
                break
        items = [
            serialize_recommendation_item(
                video,
                profile=profiles.get(video.id) or build_recommendation_profile(video),
                score=0,
                reason_code="recent",
                reason_label="最近内容",
                reason_text="当前暂无更强信号，先从最近进入的视频继续学习。",
            )
            for video in recent_videos
        ]

    internal_item_count = count_internal_items(items)
    external_item_count = count_external_items(items)
    external_failed_provider_count = count_failed_external_providers(external_provider_summaries)
    external_fetch_failed = external_failed_provider_count > 0
    coach_summary = (
        build_template_coach_summary(
            scene=normalized_scene,
            item_count=len(items),
            include_external=include_external,
            external_fetch_failed=external_fetch_failed,
            external_failed_provider_count=external_failed_provider_count,
        )
        if coach
        else None
    )
    payload: dict = {
        "contract_version": contract_version,
        "scene": normalized_scene,
        "strategy": "video_status_interest_external_v2" if include_external else "video_status_interest_v1",
        "personalized": bool(user_tokens),
        "fallback_used": fallback_used,
        "seed_video_id": seed_video.id if seed_video is not None else None,
        "internal_item_count": internal_item_count,
        "external_item_count": external_item_count,
        "external_failed_provider_count": external_failed_provider_count,
        "external_fetch_failed": external_fetch_failed,
        "sources": summarize_recommendation_sources(items),
        "external_query": external_query_summary,
        "external_providers": summarize_external_providers(external_provider_summaries),
        "items": items,
        "coach_summary": coach_summary,
    }
    if contract_version == "1":
        payload["seed_video_title"] = seed_video.title if seed_video is not None else None
    return payload
