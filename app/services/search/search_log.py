"""语义搜索全局检索写库（失败不影响主流程）"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation
from typing import List
from typing import Optional

from app.models.semantic_search_log import SemanticSearchLog
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 表缺失时仅打一次 WARNING，避免每次全局搜索刷屏；其余失败仍按原逻辑记录。
_MISSING_SEMANTIC_SEARCH_LOGS_TABLE_WARNED = False


def _semantic_search_logs_table_missing(exc: BaseException) -> bool:
    """判断是否为 MySQL 表不存在（如 1146）导致的全局检索日志写入失败。"""
    parts: list[str] = [str(exc).lower()]
    if exc.__cause__ is not None:
        parts.append(str(exc.__cause__).lower())
    text = " ".join(parts)
    if "semantic_search_logs" not in text:
        return False
    return "doesn't exist" in text or "does not exist" in text or "1146" in text or "no such table" in text


DEFAULT_LIMIT_USED = 10
DEFAULT_THRESHOLD_USED = Decimal("0.500")
THRESHOLD_QUANTIZE = Decimal("0.001")


def normalize_limit_used(limit_used: Optional[int]) -> int:
    """统一 limit 落库值，避免上游传入 None 或非法值。"""
    if limit_used is None:
        return DEFAULT_LIMIT_USED
    try:
        return max(1, int(limit_used))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT_USED


def normalize_threshold_used(threshold_used: Optional[float | Decimal]) -> Decimal:
    """统一 threshold 落库值，使用 Decimal 保持与 MySQL DECIMAL 语义一致。"""
    if threshold_used is None:
        return DEFAULT_THRESHOLD_USED
    try:
        normalized = Decimal(str(threshold_used)).quantize(THRESHOLD_QUANTIZE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return DEFAULT_THRESHOLD_USED
    return min(max(normalized, Decimal("0.000")), Decimal("1.000"))


def normalize_video_ids_searched(video_ids_searched: Optional[List[int]]) -> Optional[List[int]]:
    """过滤并标准化实际参与检索的视频 ID。"""
    normalized: List[int] = []
    for video_id in video_ids_searched or []:
        try:
            normalized.append(int(video_id))
        except (TypeError, ValueError):
            continue
    return normalized or None


def record_global_semantic_search(
    db: Session,
    *,
    user_id: int,
    query_text: str,
    video_ids_searched: Optional[List[int]],
    result_count: int,
    total_time_ms: int,
    limit_used: Optional[int],
    threshold_used: Optional[float | Decimal],
) -> None:
    """
    将跨视频（全局）语义搜索写入 semantic_search_logs。

    任意异常仅记录日志，不向调用方抛出，避免影响搜索接口。
    """
    normalized_video_ids = normalize_video_ids_searched(video_ids_searched)
    normalized_limit_used = normalize_limit_used(limit_used)
    normalized_threshold_used = normalize_threshold_used(threshold_used)
    normalized_query_text = query_text[:500]
    try:
        row = SemanticSearchLog(
            user_id=user_id,
            query_text=normalized_query_text,
            is_global=True,
            video_ids_searched=normalized_video_ids,
            result_count=max(0, int(result_count)),
            total_time_ms=max(0, int(total_time_ms)),
            limit_used=normalized_limit_used,
            threshold_used=normalized_threshold_used,
        )
        db.add(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        global _MISSING_SEMANTIC_SEARCH_LOGS_TABLE_WARNED
        if _semantic_search_logs_table_missing(exc):
            if not _MISSING_SEMANTIC_SEARCH_LOGS_TABLE_WARNED:
                _MISSING_SEMANTIC_SEARCH_LOGS_TABLE_WARNED = True
                logger.warning(
                    "semantic_search_logs 表不存在，全局搜索审计无法落库（搜索接口仍可用）。"
                    "请执行 backend_fastapi/migrations/add_semantic_search_logs.sql，"
                    "或运行 python backend_fastapi/scripts/init_db.py --create；"
                    "说明见 SEMANTIC_SEARCH_DEPLOYMENT.md。"
                )
            else:
                logger.debug("record_global_semantic_search skipped (semantic_search_logs missing): %s", exc)
        else:
            logger.warning(
                "record_global_semantic_search failed user=%s query_len=%s video_count=%s limit=%s threshold=%s: %s",
                user_id,
                len(normalized_query_text),
                len(normalized_video_ids or []),
                normalized_limit_used,
                normalized_threshold_used,
                exc,
                exc_info=True,
            )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def is_global_semantic_search_request(video_ids: Optional[List[int]]) -> bool:
    """请求未携带或携带空 video_ids 时视为全局（跨视频）检索。"""
    return not video_ids


def maybe_record_global_semantic_search(
    db: Session,
    *,
    is_global_request: bool,
    user_id: int,
    query_text: str,
    video_ids_searched: Optional[List[int]],
    result_count: int,
    total_time_ms: int,
    limit_used: Optional[int],
    threshold_used: Optional[float | Decimal],
) -> None:
    """仅在全局搜索场景下写库，减少路由层重复分支。"""
    if not is_global_request:
        return
    record_global_semantic_search(
        db,
        user_id=user_id,
        query_text=query_text,
        video_ids_searched=video_ids_searched,
        result_count=result_count,
        total_time_ms=total_time_ms,
        limit_used=limit_used,
        threshold_used=threshold_used,
    )
