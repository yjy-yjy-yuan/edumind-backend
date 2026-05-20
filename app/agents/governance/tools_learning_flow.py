"""学习流编排允许的工具实现（仅由 governance.gateway 调用）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.exceptions import GovernanceError
from app.agents.governance.context import ensure_in_governance_context
from app.core.config import settings
from app.models.note import Note, NoteTimestamp
from app.models.video import Video
from app.services.llm_clients.qwen_vl_cloud import (
    QwenVLCloudClient,
    QwenVLCloudClientError,
)
from app.services.llm_clients.vinci_adapter import (
    VinciAdapterError,
    VinciAdapterService,
)
from app.services.video.content import (
    fallback_summary,
    fallback_tags,
    normalize_summary_style,
)
from app.utils.subtitle_io import repair_mojibake_text

_frame_desc_debug_logger = None


def _get_frame_desc_debug_logger():
    global _frame_desc_debug_logger
    if _frame_desc_debug_logger is None:
        from app.services.frame_desc.debug import get_frame_description_debug_logger

        _frame_desc_debug_logger = get_frame_description_debug_logger()
    return _frame_desc_debug_logger


def _estimate_tokens(text: str) -> int:
    s = str(text or "")
    return max(1, len(s) // 4 + 1)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _cloud_qwen_vl_enabled() -> bool:
    provider = str(getattr(settings, "FRAME_DESC_CLOUD_PROVIDER", "qwen") or "qwen").lower()
    return _as_bool(getattr(settings, "FRAME_DESC_CLOUD_FALLBACK_ENABLED", False)) and provider == "qwen"


def _call_cloud_qwen_vl(
    *,
    prompt: str,
    safe_frames: list[str],
    session_id: str,
    trace_id: str,
    fallback_reason: str,
) -> str:
    if not _cloud_qwen_vl_enabled():
        raise GovernanceError("cloud_qwen_vl_fallback_disabled")
    _get_frame_desc_debug_logger().debug(
        "fallback_reason=%s | fallback_target=cloud_qwen_vl | session_id=%s | trace_id=%s | base64_frames_count=%d",
        str(fallback_reason or "")[:240],
        session_id,
        trace_id,
        len(safe_frames),
    )
    try:
        return QwenVLCloudClient().describe(
            base64_frames=safe_frames,
            prompt=prompt,
            session_id=session_id,
            trace_id=trace_id,
        )
    except QwenVLCloudClientError as exc:
        _get_frame_desc_debug_logger().debug(
            "tool_lf_frame_description cloud_qwen_vl exception | session_id=%s | trace_id=%s | error=%s",
            session_id,
            trace_id,
            exc,
            exc_info=True,
        )
        raise GovernanceError(f"cloud_qwen_vl_failed:{exc}") from exc


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
    st = None if subtitle_text is None else repair_mojibake_text(str(subtitle_text))[:2000]

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
    """通过治理网关调用画面描述服务执行帧描述（不可绕过）。

    根据 settings.FRAME_DESC_BACKEND 路由至对应后端：
    - qwen3vl: 调用 Qwen3VLRealtimeClient.describe()
    - vinci:   调用 VinciAdapterService.request_vision_chat()（历史兼容路径）

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

    backend = str(getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl") or "qwen3vl").lower()
    _get_frame_desc_debug_logger().debug(
        "tool_lf_frame_description start | session_id=%s | trace_id=%s | FRAME_DESC_BACKEND=%s | backend=%s | base64_frames_count=%d | prompt_length=%d | empty_frames=%s",
        session_id,
        trace_id,
        getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl"),
        backend,
        len(safe_frames),
        len(prompt),
        len(safe_frames) == 0,
    )

    if backend == "qwen3vl":
        _get_frame_desc_debug_logger().debug(
            "routing to qwen3vl backend | session_id=%s | trace_id=%s | base64_frames_count=%d",
            session_id,
            trace_id,
            len(safe_frames),
        )
        _get_frame_desc_debug_logger().debug(
            "VinciAdapterService NOT instantiated | session_id=%s | trace_id=%s",
            session_id,
            trace_id,
        )
        if not safe_frames:
            _get_frame_desc_debug_logger().debug(
                "using qwen3vl text-only mode | session_id=%s | trace_id=%s | base64_frames_count=0",
                session_id,
                trace_id,
            )
        from app.services.llm_clients.qwen3vl import Qwen3VLRealtimeClient

        client = Qwen3VLRealtimeClient()
        used_cloud_fallback = False
        try:
            answer = client.describe(
                base64_frames=safe_frames,
                prompt=prompt,
            )
        except Exception as exc:  # noqa: BLE001
            _get_frame_desc_debug_logger().debug(
                "tool_lf_frame_description qwen3vl exception | session_id=%s | trace_id=%s | error=%s",
                session_id,
                trace_id,
                exc,
                exc_info=True,
            )
            if _cloud_qwen_vl_enabled():
                try:
                    answer = _call_cloud_qwen_vl(
                        prompt=prompt,
                        safe_frames=safe_frames,
                        session_id=session_id,
                        trace_id=trace_id,
                        fallback_reason=f"qwen3vl_call_failed:{exc}",
                    )
                    used_cloud_fallback = True
                except GovernanceError:
                    raise GovernanceError(f"qwen3vl_call_failed:{exc}") from exc
            else:
                raise GovernanceError(f"qwen3vl_call_failed:{exc}") from exc

        payload: dict[str, Any] = {
            "answer": answer,
            "session_id": session_id,
            "trace_id": trace_id,
            "history": safe_history,
            "backend": "cloud_qwen_vl" if used_cloud_fallback else "qwen3vl",
        }
        payload["tokens_estimated"] = _estimate_tokens(prompt) + _estimate_tokens(str(answer or ""))
        payload["frame_count"] = len(safe_frames)
        _get_frame_desc_debug_logger().debug(
            "tool_lf_frame_description result | session_id=%s | trace_id=%s | backend=qwen3vl | result_type=%s | answer_length=%d | base64_frames_count=%d",
            session_id,
            trace_id,
            type(payload).__name__,
            len(str(answer or "")),
            len(safe_frames),
        )
        return payload

    # vinci path — 历史兼容路径（仅当 FRAME_DESC_BACKEND=vinci 时触发）
    _get_frame_desc_debug_logger().debug(
        "routing to vinci backend | session_id=%s | trace_id=%s | base64_frames_count=%d",
        session_id,
        trace_id,
        len(safe_frames),
    )
    _get_frame_desc_debug_logger().debug(
        "entering vinci fallback | session_id=%s | trace_id=%s | fallback_reason=FRAME_DESC_BACKEND_vinci",
        session_id,
        trace_id,
    )
    service = VinciAdapterService()
    try:
        if safe_frames:
            response = service.request_vision_chat(
                prompt=prompt,
                base64_frames=safe_frames,
                history=safe_history,
                session_id=session_id,
                trace_id=trace_id,
            )
        else:
            response = service.request_vision_chat(
                prompt=prompt,
                base64_frames=[],
                history=safe_history,
                session_id=session_id,
                trace_id=trace_id,
                silent=True,
            )
    except VinciAdapterError as exc:
        _get_frame_desc_debug_logger().debug(
            "tool_lf_frame_description vinci exception | session_id=%s | trace_id=%s | error_code=%s | error=%s",
            session_id,
            trace_id,
            getattr(exc, "error_code", ""),
            exc,
            exc_info=True,
        )
        raise GovernanceError(f"vinci_call_failed:{exc.error_code}") from exc

    payload = dict(response or {})
    payload.setdefault("session_id", session_id)
    payload.setdefault("trace_id", trace_id)
    payload.setdefault("history", safe_history)
    payload["tokens_estimated"] = _estimate_tokens(prompt) + _estimate_tokens(str(payload.get("answer") or ""))
    payload["frame_count"] = len(safe_frames)
    _get_frame_desc_debug_logger().debug(
        "tool_lf_frame_description result | session_id=%s | trace_id=%s | backend=vinci | result_type=%s | answer_length=%d | base64_frames_count=%d",
        session_id,
        trace_id,
        type(payload).__name__,
        len(str(payload.get("answer") or "")),
        len(safe_frames),
    )
    return payload


def tool_lf_frame_description_stream(db: Session, params: dict[str, Any]):
    """通过治理网关调用画面描述服务执行流式帧描述（不可绕过）。

    根据 settings.FRAME_DESC_BACKEND 路由至对应后端：
    - qwen3vl: 调用 Qwen3VLRealtimeClient.stream_describe()
    - vinci:   调用 VinciAdapterService.stream_vision_chat()（历史兼容路径）
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

    backend = str(getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl") or "qwen3vl").lower()
    _get_frame_desc_debug_logger().debug(
        "tool_lf_frame_description_stream start | session_id=%s | trace_id=%s | FRAME_DESC_BACKEND=%s | backend=%s | base64_frames_count=%d | prompt_length=%d | empty_frames=%s",
        session_id,
        trace_id,
        getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl"),
        backend,
        len(safe_frames),
        len(prompt),
        len(safe_frames) == 0,
    )

    if backend == "qwen3vl":
        _get_frame_desc_debug_logger().debug(
            "routing to qwen3vl backend | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=%d",
            session_id,
            trace_id,
            len(safe_frames),
        )
        _get_frame_desc_debug_logger().debug(
            "VinciAdapterService NOT instantiated | stream_mode=start | session_id=%s | trace_id=%s",
            session_id,
            trace_id,
        )
        if not safe_frames:
            _get_frame_desc_debug_logger().debug(
                "using qwen3vl text-only mode | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=0",
                session_id,
                trace_id,
            )
        from app.services.llm_clients.qwen3vl import Qwen3VLRealtimeClient

        client = Qwen3VLRealtimeClient()
        try:
            for event in client.stream_describe(
                base64_frames=safe_frames,
                prompt=prompt,
            ):
                _get_frame_desc_debug_logger().debug(
                    "tool_lf_frame_description_stream event | session_id=%s | trace_id=%s | backend=qwen3vl | event_type=%s",
                    session_id,
                    trace_id,
                    str(event.get("event") or event.get("type") or ""),
                )
                yield event
            _get_frame_desc_debug_logger().debug(
                "tool_lf_frame_description_stream end | session_id=%s | trace_id=%s | backend=qwen3vl",
                session_id,
                trace_id,
            )
        except Exception as exc:  # noqa: BLE001
            _get_frame_desc_debug_logger().debug(
                "tool_lf_frame_description_stream qwen3vl exception | session_id=%s | trace_id=%s | error=%s",
                session_id,
                trace_id,
                exc,
                exc_info=True,
            )
            if _cloud_qwen_vl_enabled():
                answer = _call_cloud_qwen_vl(
                    prompt=prompt,
                    safe_frames=safe_frames,
                    session_id=session_id,
                    trace_id=trace_id,
                    fallback_reason=f"qwen3vl_stream_failed:{exc}",
                )
                yield {
                    "event": "delta",
                    "delta": answer,
                    "backend": "cloud_qwen_vl",
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
                yield {
                    "event": "done",
                    "backend": "cloud_qwen_vl",
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
            else:
                raise
        return

    # vinci path — 历史兼容路径
    _get_frame_desc_debug_logger().debug(
        "routing to vinci backend | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=%d",
        session_id,
        trace_id,
        len(safe_frames),
    )
    _get_frame_desc_debug_logger().debug(
        "entering vinci fallback | stream_mode=start | session_id=%s | trace_id=%s | fallback_reason=FRAME_DESC_BACKEND_vinci",
        session_id,
        trace_id,
    )
    service = VinciAdapterService()
    try:
        for event in service.stream_vision_chat(
            prompt=prompt,
            base64_frames=safe_frames,
            history=safe_history,
            session_id=session_id,
            trace_id=trace_id,
            silent=False,
        ):
            _get_frame_desc_debug_logger().debug(
                "tool_lf_frame_description_stream event | session_id=%s | trace_id=%s | backend=vinci | event_type=%s",
                session_id,
                trace_id,
                str(event.get("event") or event.get("type") or ""),
            )
            yield event
        _get_frame_desc_debug_logger().debug(
            "tool_lf_frame_description_stream end | session_id=%s | trace_id=%s | backend=vinci",
            session_id,
            trace_id,
        )
    except Exception as exc:  # noqa: BLE001
        _get_frame_desc_debug_logger().debug(
            "tool_lf_frame_description_stream vinci exception | session_id=%s | trace_id=%s | error=%s",
            session_id,
            trace_id,
            exc,
            exc_info=True,
        )
        raise


def tool_lf_vinci_chat(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """通过治理网关调用 Vinci 适配层（不可绕过）。

    注意：此工具仅用于通用文本对话，不走画面描述服务。
    画面描述请使用 lf_frame_description 工具（自动路由至 qwen3vl/vinci）。
    """
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
