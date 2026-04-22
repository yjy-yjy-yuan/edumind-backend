"""学习流编排允许的工具实现（仅由 governance.gateway 调用）。"""

from __future__ import annotations

from typing import Any

from app.models.note import Note
from app.models.note import NoteTimestamp
from app.services.video_content_service import fallback_summary
from app.services.video_content_service import fallback_tags
from app.services.video_content_service import normalize_summary_style
from sqlalchemy.orm import Session


def _estimate_tokens(text: str) -> int:
    s = str(text or "")
    return max(1, len(s) // 4 + 1)


def tool_lf_generate_summary_fallback(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """基于片段种子生成短摘要（本地 fallback，无外部 API）。"""
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
    video_id = int(params["video_id"])
    title = str(params.get("title") or "")[:500]
    content = str(params.get("content") or "")[:50000]
    note_type = str(params.get("note_type") or "text")[:32]
    tags = str(params.get("tags") or "")[:2000]
    keywords = str(params.get("keywords") or "")[:2000]

    note = Note(
        title=title,
        content=content,
        note_type=note_type,
        video_id=video_id,
        tags=tags,
        keywords=keywords,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {"note_id": note.id, "tokens_estimated": _estimate_tokens(title) + _estimate_tokens(content)}


def tool_lf_create_timestamp(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """为笔记绑定时间戳（写库）。"""
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
    return {"timestamp_id": ts.id, "time_seconds": ts.time_seconds, "tokens_estimated": 8}
