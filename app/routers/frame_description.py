"""实时画面描述路由 - 流式 NDJSON 端点。"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.video import Video
from app.schemas.frame_description import (
    FrameDescriptionRequest,
    FrameDescriptionSessionRequest,
    FrameDescriptionSessionResponse,
)
from app.services.frame_description_service import (
    FrameDescConfigError,
    FrameDescriptionService,
    FrameDescServiceError,
)
from app.services.vinci_adapter_service import VinciAdapterService, VinciHealthResult

logger = logging.getLogger(__name__)

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


@router.post("/describe")
async def describe_frame(
    request: FrameDescriptionRequest,
    authorization: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """实时画面描述流式端点。

    前端以固定间隔发送视频帧（base64 JPEG），后端返回 NDJSON 流式描述事件。
    事件序列：status → (description × N) → complete
    降级/错误时：error
    """
    if not getattr(settings, "FRAME_DESC_ENABLED", False):
        raise HTTPException(
            status_code=503,
            detail="实时画面描述功能未启用（FRAME_DESC_ENABLED=False）",
        )

    video = db.query(Video).filter(Video.id == request.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    trace_id = str(uuid.uuid4())[:32]

    def generate():
        service = get_frame_desc_service()

        try:
            for event in service.describe_frames(
                frames=request.frames,
                timestamp=request.timestamp,
                video_id=request.video_id,
                video_title=request.video_title or str(getattr(video, "title", "") or ""),
                detail_level=request.detail_level,
                session_id=request.session_id or str(uuid.uuid4()),
                trace_id=trace_id,
                context_history=list(request.context_history or []),
                allow_degrade=request.allow_degrade,
                db=db,
            ):
                logger.debug(
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
    3. Vinci 实际可达性（通过 VinciAdapterService.health_check()）

    返回的 vinci.reachable 字段是实际探测结果，运维可据此判断是否需要干预。
    """
    import time

    enabled = bool(getattr(settings, "FRAME_DESC_ENABLED", False))
    service = get_frame_desc_service()

    health_result: VinciHealthResult = VinciHealthResult(reachable=False, latency_ms=-1.0)
    if enabled and service is not None:
        start = time.perf_counter()
        try:
            adapter = VinciAdapterService()
            health_result = adapter.health_check(timeout_seconds=5.0)
        except Exception:
            health_result = VinciHealthResult(
                reachable=False,
                latency_ms=round((time.perf_counter() - start) * 1000, 3),
                error="health_check_internal_error",
                error_code="INTERNAL_ERROR",
            )

    return {
        "enabled": enabled,
        "service": "active" if service else "inactive",
        "description": "实时画面描述服务" if enabled else "功能未启用",
        "vinci": {
            "reachable": health_result.reachable,
            "latency_ms": health_result.latency_ms,
            "error": health_result.error,
            "error_code": health_result.error_code,
        },
    }
