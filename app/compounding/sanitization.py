"""导出前脱敏：用户标识、文本裁剪。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Optional


@dataclass(frozen=True)
class SanitizerConfig:
    """可配置裁剪与哈希策略（默认保守）。"""

    query_text_max_chars: int = 200
    tag_max_chars: int = 64
    error_message_max_chars: int = 120
    user_id_hash_salt: str = "edumind_compounding_v1"


def default_sanitizer_config() -> SanitizerConfig:
    """
    从 Settings 读取可配置项，避免环境间哈希口径不一致。
    """
    try:
        from app.core.config import settings

        return SanitizerConfig(
            query_text_max_chars=int(getattr(settings, "COMPOUNDING_QUERY_TEXT_MAX_CHARS", 200)),
            tag_max_chars=int(getattr(settings, "COMPOUNDING_TAG_MAX_CHARS", 64)),
            error_message_max_chars=int(getattr(settings, "COMPOUNDING_ERROR_MESSAGE_MAX_CHARS", 120)),
            user_id_hash_salt=str(
                getattr(settings, "COMPOUNDING_USER_ID_HASH_SALT", "edumind_compounding_v1") or "edumind_compounding_v1"
            ),
        )
    except Exception:
        return SanitizerConfig()


def _hash_user_id(user_id: int, salt: str) -> str:
    raw = f"{salt}:{user_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def truncate_text(text: Optional[str], max_chars: int) -> tuple[str, bool]:
    if text is None:
        return "", False
    s = str(text)
    if len(s) <= max_chars:
        return s, False
    return s[:max_chars], True


def sanitize_search_features(
    row: Any,
    cfg: Optional[SanitizerConfig] = None,
) -> Dict[str, Any]:
    """SemanticSearchLog 行 → 脱敏 features。"""
    cfg = cfg or default_sanitizer_config()
    q, q_trunc = truncate_text(getattr(row, "query_text", None), cfg.query_text_max_chars)
    uid = getattr(row, "user_id", None)
    trace_id = str(getattr(row, "trace_id", "") or "")
    return {
        "user_id_hash": _hash_user_id(int(uid), cfg.user_id_hash_salt) if uid is not None else None,
        "query_text": q,
        "query_text_truncated": q_trunc,
        "trace_id": trace_id,
        "trace_id_present": bool(trace_id),
        "is_global": bool(getattr(row, "is_global", False)),
        "result_count": int(getattr(row, "result_count", 0) or 0),
        "total_time_ms": int(getattr(row, "total_time_ms", 0) or 0),
        "limit_used": int(getattr(row, "limit_used", 0) or 0),
        "threshold_used": float(getattr(row, "threshold_used", 0) or 0),
        "video_ids_searched_count": len(getattr(row, "video_ids_searched", None) or []),
    }


def sanitize_similarity_features(
    row: Any,
    cfg: Optional[SanitizerConfig] = None,
) -> Dict[str, Any]:
    """SimilarityAuditLogModel 行 → 脱敏 features。"""
    cfg = cfg or default_sanitizer_config()
    t1, t1t = truncate_text(getattr(row, "tag1", None), cfg.tag_max_chars)
    t2, t2t = truncate_text(getattr(row, "tag2", None), cfg.tag_max_chars)
    err, et = truncate_text(getattr(row, "error_message", None), cfg.error_message_max_chars)
    return {
        "tag1": t1,
        "tag1_truncated": t1t,
        "tag2": t2,
        "tag2_truncated": t2t,
        "event_type": getattr(row, "event_type", None),
        "provider": getattr(row, "provider", None),
        "model": getattr(row, "model", None),
        "prompt_version": getattr(row, "prompt_version", None),
        "success": bool(getattr(row, "success", False)),
        "score": getattr(row, "score", None),
        "latency_ms": float(getattr(row, "latency_ms", 0) or 0),
        "parse_ok": bool(getattr(row, "parse_ok", False)),
        "parse_error_type": getattr(row, "parse_error_type", None),
        "fallback_reason": getattr(row, "fallback_reason", None),
        "error_message": err,
        "error_message_truncated": et,
    }
