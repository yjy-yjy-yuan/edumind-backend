"""视频推荐路由。"""

import time
import uuid
from typing import Optional
from typing import Union
from urllib.parse import urlparse

from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus
from app.core.config import settings
from app.core.database import get_db
from app.models.video import Video
from app.schemas.recommendation import RecommendationOpsMetricsResponse
from app.schemas.recommendation import RecommendationSceneListResponse
from app.schemas.recommendation import VideoRecommendationResponse
from app.schemas.recommendation import VideoRecommendationResponseV1
from app.schemas.video import VideoUploadResponse
from app.schemas.video import VideoUploadURL
from app.services.recommendation_ops_service import build_recommendation_ops_metrics
from app.services.recommendation_ops_service import record_recommendation_event
from app.services.video_api_service import build_processing_options
from app.services.video_api_service import serialize_video
from app.services.video_recommendation_service import SCENE_MAP
from app.services.video_recommendation_service import list_recommendation_scenes
from app.services.video_recommendation_service import load_candidate_videos_for_recommendation
from app.services.video_recommendation_service import normalize_scene
from app.services.video_recommendation_service import recommend_videos
from app.services.video_recommendation_service import sanitize_recommendation_payload_for_client
from app.services.video_recommendation_service import summarize_recommendation_sources
from app.services.video_url_import_service import import_remote_video_from_url
from app.utils.auth_deps import resolve_user_from_request
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from sqlalchemy.orm import Session

router = APIRouter()


def _resolve_trace_id(request: Request) -> str:
    raw = request.headers.get("X-Trace-Id") or request.headers.get("X-Request-Id")
    if raw and str(raw).strip():
        return str(raw).strip()[:128]
    return str(uuid.uuid4())


def _attach_trace_headers(response: Response, trace_id: str) -> None:
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Request-Id"] = trace_id


def _emit_recommendation_event(
    *,
    trace_id: str,
    event_type: str,
    status: str,
    latency_ms: Optional[float] = None,
    metadata: Optional[dict] = None,
    db: Optional[Session] = None,
) -> None:
    try:
        record_recommendation_event(
            event_type=event_type,
            status=status,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
            db=db,
        )
    except Exception:
        pass
    if not getattr(settings, "RECOMMENDATION_TELEMETRY_ENABLED", True):
        return
    tid = (trace_id or "").strip() or settings.ANALYTICS_TRACE_ID_PLACEHOLDER
    try:
        get_telemetry().emit(
            AnalyticsEvent(
                event_type=event_type,
                trace_id=tid[:128],
                module="recommendation",
                status=status,
                latency_ms=latency_ms,
                metadata=dict(metadata or {}),
            )
        )
    except Exception:
        return


def parse_exclude_ids(raw_value: Optional[str]) -> set[int]:
    """解析逗号分隔的排除视频 ID 列表。"""
    values = set()
    for chunk in str(raw_value or "").split(","):
        text = chunk.strip()
        if text.isdigit():
            values.add(int(text))
    return values


def _coerce_video_recommendation_response(
    payload: dict,
) -> Union[VideoRecommendationResponse, VideoRecommendationResponseV1]:
    """契约版本以服务端配置为准，避免 payload 内 contract_version 漂移；v2 不序列化 seed_video_title。"""
    cv = str(settings.RECOMMENDATION_CONTRACT_VERSION or "2").strip()
    merged = {**payload, "contract_version": cv}
    if cv == "1":
        return VideoRecommendationResponseV1.model_validate(merged)
    cleaned = {k: v for k, v in merged.items() if k != "seed_video_title"}
    return VideoRecommendationResponse.model_validate(cleaned)


def _url_host(url: str) -> str:
    """Extract hostname for telemetry (not a truncated URL)."""
    try:
        host = urlparse(url or "").hostname or ""
    except ValueError:
        host = ""
    return (host or "")[:120]


def _is_external_import_candidate(item: dict) -> bool:
    """判断是否为可自动入库的站外候选。"""
    if not isinstance(item, dict):
        return False
    if not bool(item.get("is_external")):
        return False
    if str(item.get("item_type") or "").strip().lower() != "external_candidate":
        return False
    if not bool(item.get("can_import")):
        return False
    return bool(str(item.get("external_url") or "").strip())


def _serialize_materialized_recommendation_item(*, source_item: dict, video: Video, duplicate: bool) -> dict:
    """把站外候选自动入库结果转成可直接打开的视频推荐项。"""
    payload = serialize_video(video)
    external_source_label = str(
        source_item.get("source_label") or source_item.get("external_source_label") or "站外推荐"
    ).strip()
    source_label = f"已入库·{external_source_label}" if external_source_label else "已入库·站外推荐"
    status = str(payload.get("status") or "")
    processing_origin = payload.get("processing_origin")
    return {
        "id": video.id,
        "title": payload.get("title") or source_item.get("title"),
        "status": status,
        "upload_time": video.upload_time,
        "summary": "",
        "tags": [],
        "process_progress": float(payload.get("process_progress") or 0),
        "current_step": str(payload.get("current_step") or ""),
        "processing_origin": processing_origin,
        "processing_origin_label": payload.get("processing_origin_label"),
        "upload_source": payload.get("upload_source"),
        "upload_source_label": payload.get("upload_source_label"),
        "recommendation_score": float(source_item.get("recommendation_score") or 0),
        "reason_code": str(source_item.get("reason_code") or "external_discovery"),
        "reason_label": str(source_item.get("reason_label") or "已导入"),
        "reason_text": "",
        "is_external": False,
        "item_type": "video",
        "source_label": source_label,
        "external_source_label": external_source_label or None,
        "external_url": str(source_item.get("external_url") or "").strip() or None,
        "subject": source_item.get("subject"),
        "cluster_key": source_item.get("cluster_key"),
        "author": source_item.get("author"),
        "provider": "internal",
        "can_import": False,
        "import_hint": (
            "该推荐已存在于你的视频库中，已直接对齐到现有记录。"
            if duplicate
            else "该推荐已自动入库，可直接打开详情继续处理。"
        ),
        "action_type": "open_video_detail",
        "action_label": "打开详情",
        "action_target": f"/videos/{video.id}",
        "action_api": None,
        "action_method": None,
        "materialized_from_external": True,
        "materialization_status": "reused" if duplicate else "created",
    }


def _refresh_payload_counters(payload: dict) -> None:
    """在条目被自动入库替换后，刷新计数与来源摘要。"""
    items = list(payload.get("items") or [])
    payload["internal_item_count"] = sum(1 for item in items if not bool(item.get("is_external")))
    payload["external_item_count"] = sum(1 for item in items if bool(item.get("is_external")))
    payload["sources"] = summarize_recommendation_sources(items)


def _filter_forbidden_video_ids(payload: dict, forbidden_video_ids: set[int]) -> None:
    """在路由收口阶段硬过滤禁止返回的视频 ID。"""
    if not forbidden_video_ids:
        return
    items = list(payload.get("items") or [])
    if not items:
        return
    filtered_items: list[dict] = []
    changed = False
    for item in items:
        raw_id = item.get("id")
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            filtered_items.append(item)
            continue
        if item_id in forbidden_video_ids:
            changed = True
            continue
        filtered_items.append(item)
    if not changed:
        return
    payload["items"] = filtered_items
    _refresh_payload_counters(payload)


def _auto_import_external_recommendations(
    *,
    payload: dict,
    db: Session,
    user_id: Optional[int],
    trace_id: str,
) -> dict:
    """对登录用户把可导入站外候选自动写入 videos，再返回可打开的视频条目。"""
    payload["flow_version"] = "recommendation_flow_v2"
    payload["auto_materialized_external_count"] = 0
    payload["auto_materialization_failed_count"] = 0

    if not getattr(settings, "RECOMMENDATION_AUTO_IMPORT_EXTERNAL", True):
        return payload
    if not user_id:
        return payload

    max_items = max(0, int(getattr(settings, "RECOMMENDATION_AUTO_IMPORT_MAX_ITEMS", 2)))
    if max_items <= 0:
        return payload

    process_options = build_processing_options()
    materialized_count = 0
    failed_count = 0
    attempted_count = 0
    updated_items: list[dict] = []
    for item in list(payload.get("items") or []):
        if attempted_count >= max_items or not _is_external_import_candidate(item):
            updated_items.append(item)
            continue

        attempted_count += 1
        video_url = str(item.get("external_url") or "").strip()
        try:
            result = import_remote_video_from_url(
                db,
                user_id=user_id,
                video_url=video_url,
                process_options=process_options,
                preferred_title=str(item.get("title") or "").strip(),
                preferred_summary=str(item.get("summary") or "").strip(),
                preferred_tags=list(item.get("tags") or []),
                request_source="recommendation_auto_materialize",
            )
            updated_items.append(
                _serialize_materialized_recommendation_item(
                    source_item=item,
                    video=result.video,
                    duplicate=result.duplicate,
                )
            )
            materialized_count += 1
        except Exception as exc:
            failed_count += 1
            updated_items.append(item)
            _emit_recommendation_event(
                trace_id=trace_id,
                event_type="recommendation_external_materialization_failed",
                status=AnalyticsStatus.DEGRADED.value,
                latency_ms=None,
                metadata={
                    "url_host": _url_host(video_url),
                    "error": str(exc)[:160],
                },
                db=db,
            )

    payload["items"] = updated_items
    payload["auto_materialized_external_count"] = materialized_count
    payload["auto_materialization_failed_count"] = failed_count
    _refresh_payload_counters(payload)
    if attempted_count > 0:
        _emit_recommendation_event(
            trace_id=trace_id,
            event_type="recommendation_external_materialization_completed",
            status=AnalyticsStatus.OK.value if failed_count == 0 else AnalyticsStatus.DEGRADED.value,
            latency_ms=None,
            metadata={
                "attempted_count": attempted_count,
                "materialized_count": materialized_count,
                "failed_count": failed_count,
            },
            db=db,
        )
    return payload


@router.get("/scenes", response_model=RecommendationSceneListResponse)
async def get_recommendation_scenes(request: Request, response: Response, db: Session = Depends(get_db)):
    """返回前端可直接渲染的推荐场景配置。"""
    trace_id = _resolve_trace_id(request)
    _attach_trace_headers(response, trace_id)
    t0 = time.perf_counter()
    scenes = list_recommendation_scenes()
    latency_ms = (time.perf_counter() - t0) * 1000.0
    _emit_recommendation_event(
        trace_id=trace_id,
        event_type="recommendation_scenes_served",
        status=AnalyticsStatus.OK.value,
        latency_ms=latency_ms,
        metadata={"scene_count": len(scenes)},
        db=db,
    )
    return {"message": "获取推荐场景成功", "scenes": scenes}


@router.get("/videos", response_model=Union[VideoRecommendationResponse, VideoRecommendationResponseV1])
async def get_video_recommendations(
    request: Request,
    response: Response,
    scene: str = Query(default="home", description="推荐场景：home/continue/review/related"),
    limit: int = Query(
        default=6,
        ge=1,
        le=12,
        description="返回条数（推荐接口会按后端窗口规范化到 5~8，用于稳定移动端体验）",
    ),
    include_external: bool = Query(
        default=settings.RECOMMENDATION_INCLUDE_EXTERNAL_DEFAULT,
        description="是否附带站外候选；服务端默认由 RECOMMENDATION_INCLUDE_EXTERNAL_DEFAULT 控制",
    ),
    coach: bool = Query(default=False, description="是否返回 coach_summary 模板说明"),
    seed_video_id: Optional[int] = Query(default=None, description="相关推荐的种子视频 ID"),
    exclude_video_ids: Optional[str] = Query(default=None, description="排除的视频 ID，逗号分隔"),
    user_id: Optional[int] = Query(default=None, description="兼容旧链路的用户 ID"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """返回推荐视频列表。"""
    trace_id = _resolve_trace_id(request)
    _attach_trace_headers(response, trace_id)
    t0 = time.perf_counter()
    normalized_scene = normalize_scene(scene)
    scene_option = SCENE_MAP[normalized_scene]

    _emit_recommendation_event(
        trace_id=trace_id,
        event_type="recommendation_request_received",
        status=AnalyticsStatus.OK.value,
        metadata={"scene": normalized_scene, "limit": limit, "include_external": include_external},
        db=db,
    )

    if scene_option["requires_seed"] and not seed_video_id:
        raise HTTPException(status_code=422, detail="scene=related 时必须传入 seed_video_id")

    seed_video = None
    if seed_video_id is not None:
        seed_video = db.query(Video).filter(Video.id == seed_video_id).first()
        if seed_video is None:
            raise HTTPException(status_code=404, detail="seed 视频不存在")

    excluded_video_ids = parse_exclude_ids(exclude_video_ids)
    if seed_video_id is not None:
        excluded_video_ids.add(seed_video_id)

    user = resolve_user_from_request(db, user_id, authorization)
    max_scan = int(settings.RECOMMENDATION_MAX_CANDIDATES_SCAN)
    videos = load_candidate_videos_for_recommendation(db, seed_video, max_scan)
    payload = recommend_videos(
        videos=videos,
        scene=normalized_scene,
        limit=limit,
        seed_video=seed_video,
        user=user,
        exclude_ids=excluded_video_ids,
        include_external=include_external,
        coach=coach,
        enforce_return_window=True,
    )
    if include_external:
        payload = _auto_import_external_recommendations(
            payload=payload,
            db=db,
            user_id=user.id if user is not None else None,
            trace_id=trace_id,
        )
    else:
        payload["flow_version"] = "recommendation_flow_v1"
        payload["auto_materialized_external_count"] = 0
        payload["auto_materialization_failed_count"] = 0
    _filter_forbidden_video_ids(payload, excluded_video_ids)
    payload = sanitize_recommendation_payload_for_client(payload)
    payload["message"] = "获取推荐视频成功"
    response_payload = _coerce_video_recommendation_response(payload)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    _emit_recommendation_event(
        trace_id=trace_id,
        event_type="recommendation_ranking_completed",
        status=AnalyticsStatus.OK.value,
        latency_ms=latency_ms,
        metadata={
            "scene": payload.get("scene"),
            "strategy": payload.get("strategy"),
            "personalized": payload.get("personalized"),
            "fallback_used": payload.get("fallback_used"),
            "internal_item_count": payload.get("internal_item_count"),
            "external_item_count": payload.get("external_item_count"),
            "contract_version": getattr(response_payload, "contract_version", None),
        },
        db=db,
    )
    if payload.get("fallback_used"):
        _emit_recommendation_event(
            trace_id=trace_id,
            event_type="recommendation_fallback_used",
            status=AnalyticsStatus.DEGRADED.value,
            latency_ms=None,
            metadata={"scene": payload.get("scene")},
            db=db,
        )
    if include_external:
        if payload.get("external_fetch_failed"):
            _emit_recommendation_event(
                trace_id=trace_id,
                event_type="recommendation_external_fetch_failed",
                status=AnalyticsStatus.DEGRADED.value,
                latency_ms=None,
                metadata={
                    "external_failed_provider_count": payload.get("external_failed_provider_count"),
                },
                db=db,
            )
        else:
            _emit_recommendation_event(
                trace_id=trace_id,
                event_type="recommendation_external_fetch_completed",
                status=AnalyticsStatus.OK.value,
                latency_ms=None,
                metadata={"external_item_count": payload.get("external_item_count")},
                db=db,
            )
    return response_payload


@router.get("/ops/metrics", response_model=RecommendationOpsMetricsResponse)
async def get_recommendation_ops_metrics(
    request: Request,
    response: Response,
    days: int = Query(default=7, ge=1, le=30, description="统计窗口（天）"),
    user_id: Optional[int] = Query(default=None, description="兼容旧链路的用户 ID"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """推荐运营聚合指标：导入成功率与处理完成率。"""
    trace_id = _resolve_trace_id(request)
    _attach_trace_headers(response, trace_id)
    t0 = time.perf_counter()
    user = resolve_user_from_request(db, user_id, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录后查看推荐运营指标")

    payload = build_recommendation_ops_metrics(db, window_days=days)
    payload["message"] = "获取推荐运营指标成功"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    _emit_recommendation_event(
        trace_id=trace_id,
        event_type="recommendation_ops_metrics_served",
        status=AnalyticsStatus.OK.value,
        latency_ms=latency_ms,
        metadata={
            "data_source": payload.get("data_source"),
            "window_days": payload.get("window_days"),
            "import_requested_count": payload.get("recommendation_import", {}).get("requested_count", 0),
            "import_completed_count": payload.get("recommendation_import", {}).get("completed_count", 0),
            "processing_tracked_count": payload.get("processing", {}).get("tracked_video_count", 0),
        },
        db=db,
    )
    return payload


@router.post("/import-external", response_model=VideoUploadResponse)
async def import_external_recommendation(
    request: Request,
    response: Response,
    data: VideoUploadURL,
    user_id: Optional[int] = Query(default=None, description="兼容旧链路的用户 ID"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """将推荐页中的站外候选直接提交到现有链接下载入库链路。"""
    trace_id = _resolve_trace_id(request)
    _attach_trace_headers(response, trace_id)
    t0 = time.perf_counter()
    user = resolve_user_from_request(db, user_id, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录后再导入站外视频")
    _emit_recommendation_event(
        trace_id=trace_id,
        event_type="recommendation_import_external_requested",
        status=AnalyticsStatus.STARTED.value,
        metadata={"url_host": _url_host(data.url or "")},
        db=db,
    )
    process_options = build_processing_options(
        language=data.language,
        model=data.model,
        auto_generate_summary=data.auto_generate_summary,
        auto_generate_tags=data.auto_generate_tags,
        summary_style=data.summary_style,
    )
    try:
        result = import_remote_video_from_url(
            db,
            user_id=user.id,
            video_url=data.url,
            process_options=process_options,
            preferred_title=data.title,
            preferred_summary=data.summary,
            preferred_tags=list(data.tags or []),
            request_source="recommendation_import_external",
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        _emit_recommendation_event(
            trace_id=trace_id,
            event_type="recommendation_import_external_failed",
            status=AnalyticsStatus.ERROR.value,
            latency_ms=latency_ms,
            metadata={"error": str(exc)[:200]},
            db=db,
        )
        raise
    latency_ms = (time.perf_counter() - t0) * 1000.0
    _emit_recommendation_event(
        trace_id=trace_id,
        event_type="recommendation_import_external_completed",
        status=AnalyticsStatus.OK.value,
        latency_ms=latency_ms,
        metadata={
            "video_id": result.video.id,
            "status": result.status,
            "duplicate": result.duplicate,
        },
        db=db,
    )
    return VideoUploadResponse(
        id=result.video.id,
        status=result.status,
        message=result.message,
        duplicate=result.duplicate,
        data=serialize_video(result.video),
    )
