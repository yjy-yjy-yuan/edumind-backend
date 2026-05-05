"""学习流编排允许的工具实现（仅由 governance.gateway 调用）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.exceptions import GovernanceError
from app.agents.governance.context import ensure_in_governance_context
from app.models.note import Note, NoteTimestamp
from app.models.video import Video
from app.services.video_content_service import (
    fallback_summary,
    fallback_tags,
    normalize_summary_style,
)
from app.services.vinci_adapter_service import VinciAdapterError, VinciAdapterService


def _estimate_tokens(text: str) -> int:
    s = str(text or "")
    return max(1, len(s) // 4 + 1)


def tool_lf_generate_summary_fallback(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """基于片段种子生成短摘要（本地 fallback，无外部 API）。"""
    ensure_in_governance_context()
    _ = db
    summary_seed = str(params.get("summary_seed") or "").strip()
    title = str(params.get("title") or "").strip()
    style = normalize_summary_style(str(params.get("style") or "study"))
    if not summary_seed:
        return {"summary_text": "", "tokens_estimated": 0}
    summary_text = fallback_summary(summary_seed, title=title, style=style)
    return {
        "summary_text": summary_text,
        "tokens_estimated": _estimate_tokens(summary_seed) + _estimate_tokens(summary_text),
    }


def tool_lf_persist_note(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """持久化笔记（写库）。"""
    ensure_in_governance_context()
    video_id = int(params["video_id"])
    title = str(params.get("title") or "")[:500]
    content = str(params.get("content") or "")[:50000]
    note_type = str(params.get("note_type") or "text")[:32]
    tags = str(params.get("tags") or "")[:2000]
    keywords = str(params.get("keywords") or "")[:2000]
    video = db.query(Video).filter(Video.id == video_id).first()
    if video is None:
        raise GovernanceError("video_not_found")

    note = Note(
        title=title,
        content=content,
        note_type=note_type,
        video_id=video_id,
        user_id=video.user_id,
        tags=tags,
        keywords=keywords,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {
        "note_id": note.id,
        "tokens_estimated": _estimate_tokens(title) + _estimate_tokens(content),
    }


def tool_lf_create_timestamp(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """为笔记绑定时间戳（写库）。"""
    ensure_in_governance_context()
    note_id = int(params["note_id"])
    time_seconds = float(params["time_seconds"])
    subtitle_text = params.get("subtitle_text")
    st = None if subtitle_text is None else str(subtitle_text)[:2000]

    ts = NoteTimestamp(
        note_id=note_id,
        time_seconds=time_seconds,
        subtitle_text=st,
    )
    db.add(ts)
    db.commit()
    db.refresh(ts)
    return {
        "timestamp_id": ts.id,
        "time_seconds": ts.time_seconds,
        "tokens_estimated": 8,
    }


def tool_lf_frame_description(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """通过治理网关调用 Vinci 适配层执行画面描述（不可绕过）。

    支持两种模式：
    - vision 模式：有 base64_frames → 调用 request_vision_chat（含图像）
    - text   模式：无 base64_frames   → 调用 request_chat（纯文本，降级）

    参数校验由 gateway._validate_params 完成。
    """
    ensure_in_governance_context()
    _ = db
    prompt = str(params.get("prompt") or "").strip()
    session_id = str(params.get("session_id") or "").strip()
    trace_id = str(params.get("trace_id") or "").strip()
    history = params.get("history")
    safe_history = history if isinstance(history, list) else []
    raw_frames = params.get("base64_frames")
    safe_frames: list[str] = []
    if isinstance(raw_frames, list) and raw_frames:
        for f in raw_frames:
            text = str(f or "").strip()
            if text:
                if "," in text:
                    text = text.split(",", 1)[1]
                safe_frames.append(text)

    service = VinciAdapterService()
    try:
        if safe_frames:
            # Vision 模式：带图像帧
            response = service.request_vision_chat(
                prompt=prompt,
                base64_frames=safe_frames,
                history=safe_history,
                session_id=session_id,
                trace_id=trace_id,
            )
        else:
            # Text 模式：无图像（降级或 silent=True 自动描述）
            response = service.request_vision_chat(
                prompt=prompt,
                base64_frames=[],
                history=safe_history,
                session_id=session_id,
                trace_id=trace_id,
                silent=True,
            )
    except VinciAdapterError as exc:
        raise GovernanceError(f"vinci_call_failed:{exc.error_code}") from exc

    payload = dict(response or {})
    payload.setdefault("session_id", session_id)
    payload.setdefault("trace_id", trace_id)
    payload.setdefault("history", safe_history)
    payload["tokens_estimated"] = _estimate_tokens(prompt) + _estimate_tokens(str(payload.get("answer") or ""))
    payload["frame_count"] = len(safe_frames)
    return payload


def tool_lf_frame_description_stream(db: Session, params: dict[str, Any]):
    """通过治理网关调用 Vinci 适配层执行流式画面描述（不可绕过）。"""
    ensure_in_governance_context()
    _ = db
    prompt = str(params.get("prompt") or "").strip()
    session_id = str(params.get("session_id") or "").strip()
    trace_id = str(params.get("trace_id") or "").strip()
    history = params.get("history")
    safe_history = history if isinstance(history, list) else []
    raw_frames = params.get("base64_frames")
    safe_frames: list[str] = []
    if isinstance(raw_frames, list) and raw_frames:
        for f in raw_frames:
            text = str(f or "").strip()
            if text:
                if "," in text:
                    text = text.split(",", 1)[1]
                safe_frames.append(text)

    service = VinciAdapterService()
    yield from service.stream_vision_chat(
        prompt=prompt,
        base64_frames=safe_frames,
        history=safe_history,
        session_id=session_id,
        trace_id=trace_id,
        silent=False,
    )


def tool_lf_vinci_chat(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """通过治理网关调用 Vinci 适配层（不可绕过）。"""
    ensure_in_governance_context()
    _ = db
    prompt = str(params.get("prompt") or "").strip()
    session_id = str(params.get("session_id") or "").strip()
    trace_id = str(params.get("trace_id") or "").strip()
    history = params.get("history")
    safe_history = history if isinstance(history, list) else []

    service = VinciAdapterService()
    try:
        response = service.request_chat(
            prompt=prompt,
            history=safe_history,
            session_id=session_id,
            trace_id=trace_id,
        )
    except VinciAdapterError as exc:
        raise GovernanceError(f"vinci_call_failed:{exc.error_code}") from exc

    payload = dict(response or {})
    payload.setdefault("session_id", session_id)
    payload.setdefault("trace_id", trace_id)
    payload.setdefault("history", safe_history)
    payload["tokens_estimated"] = _estimate_tokens(prompt) + _estimate_tokens(str(payload.get("answer") or ""))
    return payload
