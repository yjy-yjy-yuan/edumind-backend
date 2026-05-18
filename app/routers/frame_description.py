"""实时画面描述路由 - 流式 NDJSON 端点。"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.subtitle import Subtitle
from app.models.video import Video
from app.schemas.frame_description import (
    FrameDescriptionRequest,
    FrameDescriptionSessionRequest,
    FrameDescriptionSessionResponse,
)
from app.services.frame_desc.service import (
    FrameDescConfigError,
    FrameDescriptionService,
    FrameDescServiceError,
)
from app.services.frame_desc.source_extractor import (
    FrameSourceExtractionError,
    extract_frame_from_video_url,
)
from app.services.llm_clients.qwen3vl import Qwen3VLRealtimeClient
from app.services.llm_clients.vinci_adapter import VinciAdapterService, VinciHealthResult
from app.services.frame_desc.debug import get_frame_description_debug_logger
from app.utils.subtitle_io import repair_mojibake_text

logger = logging.getLogger(__name__)
if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
    logger.setLevel(logging.DEBUG)
frame_desc_debug_logger = get_frame_description_debug_logger()

router = APIRouter()

# 全局服务实例（lifespan 内初始化）
_frame_desc_service: Optional[FrameDescriptionService] = None


def get_frame_desc_service() -> FrameDescriptionService:
    global _frame_desc_service
    if _frame_desc_service is None:
        _frame_desc_service = FrameDescriptionService()
    return _frame_desc_service


def set_frame_desc_service(service: FrameDescriptionService) -> None:
    global _frame_desc_service
    _frame_desc_service = service  # noqa: WPS437 (global statement required for test injection)


def serialize_stream_event(event: dict) -> str:
    return f"{json.dumps(event, ensure_ascii=False)}\n"


def _format_mmss(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _build_router_degraded_text(db: Session, video_id: int, timestamp: float, error_detail: str) -> str:
    subtitles = db.query(Subtitle).filter(Subtitle.video_id == int(video_id)).order_by(Subtitle.start_time.asc()).all()
    if subtitles:
        target = float(timestamp or 0)
        best_idx = 0
        best_dist = None
        for idx, item in enumerate(subtitles):
            st = float(getattr(item, "start_time", 0) or 0)
            et = float(getattr(item, "end_time", st) or st)
            midpoint = (st + et) / 2
            dist = abs(midpoint - target)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_idx = idx
        snippets: list[str] = []
        for idx in range(max(0, best_idx - 1), min(len(subtitles), best_idx + 2)):
            text = " ".join(repair_mojibake_text(str(getattr(subtitles[idx], "text", "") or "")).split()).strip()
            if text:
                snippets.append(text if len(text) <= 60 else f"{text[:60].rstrip()}...")
        if snippets:
            return f"（降级）当前约 {_format_mmss(timestamp)}，结合附近字幕：{'；'.join(snippets)}"
    detail = str(error_detail or "").strip()
    if detail:
        return f"（降级）当前约 {_format_mmss(timestamp)}，描述服务暂不可用（{detail}）。"
    return f"（降级）当前约 {_format_mmss(timestamp)}，描述服务暂不可用。"


def _frame_desc_backend() -> str:
    value = getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl")
    backend = str(value if isinstance(value, str) else "vinci").strip().lower()
    return backend if backend in {"qwen3vl", "vinci"} else "qwen3vl"


def _settings_bool(name: str, default: bool = False) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


@router.post("/describe")
async def describe_frame(
    request: FrameDescriptionRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """实时画面描述流式端点。

    前端以固定间隔发送视频帧（base64 JPEG），后端返回 NDJSON 流式描述事件。
    事件序列：status → (description × N) → complete
    降级/错误时：error
    """
    trace_id = str(uuid.uuid4())[:32]
    if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
        frame_desc_debug_logger.debug(
            "describe request received | trace_id=%s | video_id=%s | session_id=%s | timestamp=%.3f | base64_frames_count=%d | frame_source=%s | allow_degrade=%s | backend=%s",
            trace_id,
            request.video_id,
            request.session_id,
            float(request.timestamp or 0),
            len(request.frames or []),
            bool(str(request.frame_source_url or "").strip()),
            request.allow_degrade,
            _frame_desc_backend(),
        )

    if not _settings_bool("FRAME_DESC_ENABLED", False):
        raise HTTPException(
            status_code=503,
            detail="实时画面描述功能未启用（FRAME_DESC_ENABLED=False）",
        )

    video = db.query(Video).filter(Video.id == request.video_id).first()
    if not video:
        if not _settings_bool("FRAME_DESC_ALLOW_EXTERNAL_VIDEO", False):
            raise HTTPException(status_code=404, detail="视频不存在")
        logger.info(
            "frame desc external video accepted | trace_id=%s | video_id=%s | title=%s",
            trace_id,
            request.video_id,
            str(request.video_title or "")[:120],
        )

    def generate():
        service = get_frame_desc_service()

        # 认证 token 优先级：1. Authorization header  2. 请求体 frame_source_auth_token
        auth_token = ""
        if authorization and str(authorization).strip():
            raw_auth = str(authorization).strip()
            if raw_auth.lower().startswith("bearer "):
                auth_token = raw_auth[7:].strip()
            else:
                auth_token = raw_auth
        if not auth_token and request.frame_source_auth_token:
            auth_token = str(request.frame_source_auth_token).strip()

        frame_desc_debug_logger.debug(
            "auth token resolved | trace_id=%s | has_token=%s | token_prefix=%s",
            trace_id,
            bool(auth_token),
            (auth_token[:8] + "***" if len(auth_token) > 8 else (auth_token if auth_token else "")),
        )

        try:
            frames = list(request.frames or [])
            if not frames and str(request.frame_source_url or "").strip():
                frame_desc_debug_logger.debug(
                    "start extract frames from stream | trace_id=%s | url=%s | stream_url=%s | video_id=%s | session_id=%s | timestamp=%.3f | has_auth_token=%s",
                    trace_id,
                    request.frame_source_url,
                    request.frame_source_url,
                    request.video_id,
                    request.session_id,
                    float(request.timestamp or 0),
                    bool(auth_token),
                )
                yield serialize_stream_event(
                    {
                        "type": "status",
                        "stage": "sampling",
                        "message": "正在服务端抽取视频帧",
                        "progress": 15,
                    }
                )
                try:
                    frames = [
                        extract_frame_from_video_url(
                            video_url=request.frame_source_url,
                            timestamp=request.timestamp,
                            trace_id=trace_id,
                            auth_token=auth_token,
                        )
                    ]
                    frame_desc_debug_logger.debug(
                        "server frame extracted for describe | trace_id=%s | video_id=%s | session_id=%s | base64_frames_count=%d",
                        trace_id,
                        request.video_id,
                        request.session_id,
                        len(frames),
                    )
                except FrameSourceExtractionError as exc:
                    frame_desc_debug_logger.debug(
                        "frame extract failed | error_type=server_frame_extract_failed | error=%s | stream_url=%s | url=%s | session_id=%s | trace_id=%s | fallback_reason=frame_extract_failed | fallback_target=qwen3vl_text_only",
                        str(exc),
                        request.frame_source_url,
                        request.frame_source_url,
                        request.session_id,
                        trace_id,
                        exc_info=True,
                    )
                    frames = []

            for event in service.describe_frames(
                frames=frames,
                timestamp=request.timestamp,
                video_id=request.video_id,
                video_title=request.video_title
                or str(getattr(video, "title", "") or "")
                or f"video-{request.video_id}",
                detail_level=request.detail_level,
                session_id=request.session_id or str(uuid.uuid4()),
                trace_id=trace_id,
                context_history=list(request.context_history or []),
                allow_degrade=request.allow_degrade,
                db=db,
            ):
                frame_desc_debug_logger.debug(
                    "frame_desc event | trace_id=%s | type=%s | stage=%s",
                    trace_id,
                    event.get("type"),
                    event.get("stage"),
                )
                yield serialize_stream_event(event)
        except FrameDescConfigError as exc:
            logger.error("frame desc config error | trace_id=%s | error=%s", trace_id, exc)
            yield serialize_stream_event(
                {
                    "type": "error",
                    "stage": "config",
                    "message": str(exc),
                    "detail": str(exc),
                    "progress": 100,
                    "degraded": False,
                }
            )
        except FrameDescServiceError as exc:
            logger.error("frame desc service error | trace_id=%s | error=%s", trace_id, exc)
            if bool(request.allow_degrade):
                frame_desc_debug_logger.debug(
                    "fallback_reason=%s | fallback_target=subtitle_description | trace_id=%s | session_id=%s",
                    str(exc)[:200],
                    trace_id,
                    request.session_id,
                    exc_info=True,
                )
                degraded_text = _build_router_degraded_text(db, request.video_id, request.timestamp, str(exc))
                yield serialize_stream_event(
                    {
                        "type": "status",
                        "stage": "degraded",
                        "message": f"实时描述服务不可用，已降级输出（{str(exc)[:120]}）",
                        "progress": 85,
                    }
                )
                yield serialize_stream_event(
                    {
                        "type": "description",
                        "delta": degraded_text,
                        "timestamp": request.timestamp,
                        "confidence": None,
                    }
                )
                yield serialize_stream_event(
                    {
                        "type": "complete",
                        "stage": "completed",
                        "full_description": degraded_text,
                        "timestamp": request.timestamp,
                        "confidence": None,
                        "context_summary": None,
                        "degraded": True,
                        "latency_ms": None,
                        "progress": 100,
                        "message": f"降级描述已完成（{str(exc)[:120]}）",
                    }
                )
                return
            yield serialize_stream_event(
                {
                    "type": "error",
                    "stage": "inference",
                    "message": "画面描述服务异常",
                    "detail": str(exc)[:500],
                    "progress": 100,
                    "degraded": False,
                }
            )
        except Exception as exc:
            logger.error("frame desc unexpected error | trace_id=%s | error=%s", trace_id, exc)
            if bool(request.allow_degrade):
                frame_desc_debug_logger.debug(
                    "fallback_reason=unexpected_error:%s | fallback_target=subtitle_description | trace_id=%s | session_id=%s",
                    str(exc)[:200],
                    trace_id,
                    request.session_id,
                    exc_info=True,
                )
                degraded_text = _build_router_degraded_text(db, request.video_id, request.timestamp, str(exc))
                yield serialize_stream_event(
                    {
                        "type": "status",
                        "stage": "degraded",
                        "message": f"服务异常，已降级输出（{str(exc)[:120]}）",
                        "progress": 85,
                    }
                )
                yield serialize_stream_event(
                    {
                        "type": "description",
                        "delta": degraded_text,
                        "timestamp": request.timestamp,
                        "confidence": None,
                    }
                )
                yield serialize_stream_event(
                    {
                        "type": "complete",
                        "stage": "completed",
                        "full_description": degraded_text,
                        "timestamp": request.timestamp,
                        "confidence": None,
                        "context_summary": None,
                        "degraded": True,
                        "latency_ms": None,
                        "progress": 100,
                        "message": f"降级描述已完成（{str(exc)[:120]}）",
                    }
                )
                return
            yield serialize_stream_event(
                {
                    "type": "error",
                    "stage": "server",
                    "message": "描述处理失败，请稍后重试",
                    "detail": str(exc)[:500],
                    "progress": 100,
                    "degraded": False,
                }
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"X-Trace-Id": trace_id},
    )


@router.post("/session", response_model=FrameDescriptionSessionResponse)
async def manage_session(
    request: FrameDescriptionSessionRequest,
    authorization: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """开启或关闭实时描述会话。"""
    if not getattr(settings, "FRAME_DESC_ENABLED", False):
        raise HTTPException(
            status_code=503,
            detail="实时画面描述功能未启用",
        )

    service = get_frame_desc_service()

    if request.action == "start":
        result = service.start_session(
            video_id=request.video_id,
            detail_level=request.detail_level,
            session_id=request.session_id,
        )
        return FrameDescriptionSessionResponse(**result)

    if request.action == "stop":
        if not request.session_id:
            raise HTTPException(status_code=400, detail="关闭会话必须提供 session_id")
        result = service.stop_session(session_id=request.session_id)
        return FrameDescriptionSessionResponse(**result)

    raise HTTPException(status_code=400, detail="action 仅支持 start 或 stop")


@router.get("/health")
async def frame_desc_health():
    """画面描述服务健康检查。

    包含三层检查：
    1. 功能开关（FRAME_DESC_ENABLED）
    2. 服务实例（FrameDescriptionService 实例化）
    3. 上游视觉模型服务实际可达性（Qwen3-VL 或历史 Vinci）

    返回的 upstream.reachable 字段是实际探测结果，运维可据此判断是否需要干预。
    """
    import time

    enabled = bool(getattr(settings, "FRAME_DESC_ENABLED", False))
    backend = _frame_desc_backend()
    service = get_frame_desc_service()

    health_result: VinciHealthResult = VinciHealthResult(reachable=False, latency_ms=-1.0)
    if enabled and service is not None:
        start = time.perf_counter()
        try:
            if backend == "qwen3vl":
                health_result = Qwen3VLRealtimeClient().health_check(timeout_seconds=5.0)
            else:
                health_result = VinciAdapterService().health_check(timeout_seconds=5.0)
            if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
                frame_desc_debug_logger.debug(
                    "health probe | backend=%s | reachable=%s | loaded=%s | latency_ms=%s | error_code=%s | error=%s",
                    backend,
                    getattr(health_result, "reachable", None),
                    getattr(health_result, "loaded", None),
                    getattr(health_result, "latency_ms", None),
                    getattr(health_result, "error_code", None),
                    getattr(health_result, "error", None),
                )
        except Exception:
            health_result = VinciHealthResult(
                reachable=False,
                latency_ms=round((time.perf_counter() - start) * 1000, 3),
                error="health_check_internal_error",
                error_code="INTERNAL_ERROR",
            )
            if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
                frame_desc_debug_logger.debug("health probe internal error", exc_info=True)

    return {
        "enabled": enabled,
        "service": "active" if service else "inactive",
        "description": "实时画面描述服务" if enabled else "功能未启用",
        "upstream": {
            "provider": backend,
            "reachable": health_result.reachable,
            "latency_ms": health_result.latency_ms,
            "loaded": getattr(health_result, "loaded", None),
            "model": getattr(health_result, "model", None),
            "device": getattr(health_result, "device", None),
            "error": health_result.error,
            "error_code": health_result.error_code,
        },
        "vinci": {
            "provider": backend,
            "reachable": health_result.reachable,
            "latency_ms": health_result.latency_ms,
            "error": health_result.error,
            "error_code": health_result.error_code,
        },
    }
