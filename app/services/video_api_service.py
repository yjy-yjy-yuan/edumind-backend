"""视频接口共享辅助服务。"""

from __future__ import annotations

import re
from typing import Optional

from app.core.config import settings
from app.models.video import Video
from app.services.video_content_service import normalize_summary_style
from app.services.video_processing_registry import get_video_processing_request
from app.services.whisper_runtime import normalize_whisper_model_name
from fastapi import HTTPException

MODEL_STEP_RE = re.compile(r"（([a-z0-9._-]+)）")


def build_processing_options(
    *,
    language: str = "Other",
    model: Optional[str] = None,
    auto_generate_summary: bool = True,
    auto_generate_tags: bool = True,
    summary_style: str = "study",
) -> dict:
    """规范化视频处理参数。"""
    whisper_language = "en" if str(language or "").strip() == "English" else "zh"
    auto_tags = bool(auto_generate_tags)
    try:
        normalized_model = normalize_whisper_model_name(model or settings.WHISPER_MODEL)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "language": whisper_language,
        "model": normalized_model,
        "auto_generate_summary": bool(auto_generate_summary or auto_tags),
        "auto_generate_tags": auto_tags,
        "summary_style": normalize_summary_style(summary_style),
    }


def infer_model_from_step(step: Optional[str]) -> Optional[str]:
    """从 current_step 文本里回推处理模型。"""
    text = str(step or "").strip()
    if not text:
        return None
    match = MODEL_STEP_RE.search(text)
    return match.group(1).strip() if match else None


def build_processing_metadata(video: Video) -> dict:
    """构建面向 API 返回的处理元数据。"""
    request_meta = get_video_processing_request(video.id) or {}
    requested_model = str(request_meta.get("requested_model") or "").strip() or infer_model_from_step(
        video.current_step
    )
    effective_model = str(request_meta.get("effective_model") or requested_model or "").strip() or None
    requested_language = str(request_meta.get("requested_language") or "").strip() or None
    return {
        "requested_model": requested_model or None,
        "effective_model": effective_model,
        "requested_language": requested_language,
    }


def serialize_video(video: Video) -> dict:
    """序列化视频对象，并补充处理元数据。"""
    payload = video.to_dict()
    payload.update(build_processing_metadata(video))
    return payload
