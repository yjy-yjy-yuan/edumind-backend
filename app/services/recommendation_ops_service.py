"""推荐运营面板聚合服务（P1）。"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from threading import Lock
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.recommendation_ops_event import RecommendationOpsEvent
from app.models.video import Video
from app.models.video import VideoStatus
from sqlalchemy.orm import Session

IMPORT_REQUESTED_EVENT = "recommendation_import_external_requested"
IMPORT_COMPLETED_EVENT = "recommendation_import_external_completed"
IMPORT_FAILED_EVENT = "recommendation_import_external_failed"
AUTO_MATERIALIZATION_COMPLETED_EVENT = "recommendation_external_materialization_completed"

logger = logging.getLogger(__name__)
_MISSING_RECOMMENDATION_OPS_EVENTS_TABLE_WARNED = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_datetime(value: Optional[datetime] = None) -> datetime:
    target = value or _utc_now()
    if target.tzinfo is None:
        return target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RecommendationEventSnapshot:
    """推荐域遥测事件快照。"""

    timestamp: datetime
    event_type: str
    status: str
    metadata: Dict[str, Any]


class RecommendationEventStore:
    """内存环形缓冲，用于推荐运营聚合 API。"""

    def __init__(self, max_events: int):
        self._max_events = max(100, int(max_events))
        self._rows: deque[RecommendationEventSnapshot] = deque(maxlen=self._max_events)
        self._lock = Lock()

    def record(
        self,
        *,
        event_type: str,
        status: str,
        metadata: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        row = RecommendationEventSnapshot(
            timestamp=_to_utc_datetime(timestamp),
            event_type=str(event_type or "").strip(),
            status=str(status or "").strip(),
            metadata=dict(metadata or {}),
        )
        if not row.event_type:
            return
        with self._lock:
            self._rows.append(row)

    def events_since(self, since: datetime) -> List[RecommendationEventSnapshot]:
        threshold = _to_utc_datetime(since)
        with self._lock:
            return [row for row in list(self._rows) if row.timestamp >= threshold]


_event_store: Optional[RecommendationEventStore] = None


def _recommendation_ops_events_table_missing(exc: BaseException) -> bool:
    parts: list[str] = [str(exc).lower()]
    if exc.__cause__ is not None:
        parts.append(str(exc.__cause__).lower())
    text = " ".join(parts)
    if "recommendation_ops_events" not in text:
        return False
    return "doesn't exist" in text or "does not exist" in text or "1146" in text or "no such table" in text


def _new_persistence_session(db: Optional[Session]) -> Session:
    if db is not None:
        try:
            bind = db.get_bind()
            if bind is not None:
                return Session(bind=bind)
        except Exception:
            pass
    return SessionLocal()


def _persist_recommendation_event(snapshot: RecommendationEventSnapshot, *, db: Optional[Session]) -> bool:
    event_db = _new_persistence_session(db)
    try:
        event_db.add(
            RecommendationOpsEvent(
                event_type=snapshot.event_type,
                status=snapshot.status,
                trace_id=str(snapshot.metadata.get("_trace_id") or "").strip()[:128] or None,
                metadata_json=dict(snapshot.metadata),
            )
        )
        event_db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        global _MISSING_RECOMMENDATION_OPS_EVENTS_TABLE_WARNED
        if _recommendation_ops_events_table_missing(exc):
            if not _MISSING_RECOMMENDATION_OPS_EVENTS_TABLE_WARNED:
                _MISSING_RECOMMENDATION_OPS_EVENTS_TABLE_WARNED = True
                logger.warning(
                    "recommendation_ops_events 表不存在，推荐运营聚合将降级为进程内缓冲。"
                    "请执行 backend_fastapi/migrations/add_recommendation_ops_events.sql，"
                    "或运行 python backend_fastapi/scripts/init_db.py --create。"
                )
            else:
                logger.debug("persist recommendation ops event skipped (table missing): %s", exc)
        else:
            logger.warning("persist recommendation ops event failed: %s", exc, exc_info=True)
        try:
            event_db.rollback()
        except Exception:
            pass
        return False
    finally:
        event_db.close()


def get_recommendation_event_store() -> RecommendationEventStore:
    global _event_store
    if _event_store is None:
        _event_store = RecommendationEventStore(max_events=settings.RECOMMENDATION_OPS_EVENT_BUFFER_SIZE)
    return _event_store


def reset_recommendation_event_store_for_tests() -> None:
    """测试用：重置单例状态。"""
    global _event_store
    _event_store = None


def record_recommendation_event(
    *,
    event_type: str,
    status: str,
    trace_id: str = "",
    metadata: Optional[dict] = None,
    timestamp: Optional[datetime] = None,
    db: Optional[Session] = None,
) -> None:
    """写入推荐域聚合缓冲（不影响主链路）。"""
    payload = dict(metadata or {})
    if trace_id:
        payload["_trace_id"] = str(trace_id).strip()[:128]
    snapshot = RecommendationEventSnapshot(
        timestamp=_to_utc_datetime(timestamp),
        event_type=str(event_type or "").strip(),
        status=str(status or "").strip(),
        metadata=payload,
    )
    if not snapshot.event_type:
        return
    try:
        get_recommendation_event_store().record(
            event_type=snapshot.event_type,
            status=snapshot.status,
            metadata=snapshot.metadata,
            timestamp=snapshot.timestamp,
        )
    except Exception:
        pass
    _persist_recommendation_event(snapshot, db=db)


def _load_persisted_events(db: Session, *, since: datetime) -> Optional[List[RecommendationEventSnapshot]]:
    since_naive_utc = _to_utc_datetime(since).replace(tzinfo=None)
    try:
        rows = (
            db.query(RecommendationOpsEvent)
            .filter(RecommendationOpsEvent.created_at >= since_naive_utc)
            .order_by(RecommendationOpsEvent.created_at.desc())
            .all()
        )
        snapshots: list[RecommendationEventSnapshot] = []
        for row in rows:
            snapshots.append(
                RecommendationEventSnapshot(
                    timestamp=_to_utc_datetime(row.created_at),
                    event_type=str(row.event_type or "").strip(),
                    status=str(row.status or "").strip(),
                    metadata=dict(row.metadata_json or {}),
                )
            )
        return snapshots
    except Exception as exc:  # noqa: BLE001
        if _recommendation_ops_events_table_missing(exc):
            logger.debug("query recommendation ops events skipped (table missing): %s", exc)
        else:
            logger.warning("query recommendation ops events failed: %s", exc, exc_info=True)
        return None


def _collect_import_metrics(events: List[RecommendationEventSnapshot]) -> dict:
    requested_count = sum(1 for row in events if row.event_type == IMPORT_REQUESTED_EVENT)
    completed_rows = [row for row in events if row.event_type == IMPORT_COMPLETED_EVENT]
    completed_count = len(completed_rows)
    failed_count = sum(1 for row in events if row.event_type == IMPORT_FAILED_EVENT)
    in_flight_count = max(0, requested_count - completed_count - failed_count)
    success_rate = (completed_count / requested_count) if requested_count else 0.0
    return {
        "requested_count": requested_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "in_flight_count": in_flight_count,
        "success_rate": success_rate,
        "completed_video_ids": {
            _safe_int(row.metadata.get("video_id"))
            for row in completed_rows
            if _safe_int(row.metadata.get("video_id")) > 0
        },
    }


def _collect_auto_materialization_metrics(events: List[RecommendationEventSnapshot]) -> dict:
    attempted_count = 0
    materialized_count = 0
    failed_count = 0
    for row in events:
        if row.event_type != AUTO_MATERIALIZATION_COMPLETED_EVENT:
            continue
        attempted_count += max(0, _safe_int(row.metadata.get("attempted_count")))
        materialized_count += max(0, _safe_int(row.metadata.get("materialized_count")))
        failed_count += max(0, _safe_int(row.metadata.get("failed_count")))
    success_rate = (materialized_count / attempted_count) if attempted_count else 0.0
    return {
        "attempted_count": attempted_count,
        "materialized_count": materialized_count,
        "failed_count": failed_count,
        "success_rate": success_rate,
    }


def _normalize_status(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value or "").strip().lower()
    return str(value or "").strip().lower()


def _collect_processing_metrics(db: Session, *, completed_video_ids: Set[int]) -> dict:
    ids = sorted({int(video_id) for video_id in completed_video_ids if int(video_id) > 0})
    if not ids:
        return {
            "tracked_video_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "in_progress_count": 0,
            "completion_rate": 0.0,
            "failure_rate": 0.0,
            "terminal_rate": 0.0,
            "status_breakdown": {},
        }

    rows = db.query(Video.id, Video.status).filter(Video.id.in_(ids)).all()
    status_breakdown: Dict[str, int] = {}
    for _, raw_status in rows:
        normalized = _normalize_status(raw_status)
        status_breakdown[normalized] = status_breakdown.get(normalized, 0) + 1

    completed_count = status_breakdown.get(VideoStatus.COMPLETED.value, 0)
    failed_count = status_breakdown.get(VideoStatus.FAILED.value, 0)
    tracked_video_count = len(rows)
    in_progress_count = max(0, tracked_video_count - completed_count - failed_count)
    completion_rate = (completed_count / tracked_video_count) if tracked_video_count else 0.0
    failure_rate = (failed_count / tracked_video_count) if tracked_video_count else 0.0
    terminal_rate = ((completed_count + failed_count) / tracked_video_count) if tracked_video_count else 0.0

    return {
        "tracked_video_count": tracked_video_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "in_progress_count": in_progress_count,
        "completion_rate": completion_rate,
        "failure_rate": failure_rate,
        "terminal_rate": terminal_rate,
        "status_breakdown": status_breakdown,
    }


def build_recommendation_ops_metrics(db: Session, *, window_days: int) -> dict:
    """构建推荐运营聚合视图。"""
    days = max(1, int(window_days))
    window_end = _utc_now()
    window_start = window_end - timedelta(days=days)
    rows = _load_persisted_events(db, since=window_start)
    data_source = "database"
    if rows is None:
        rows = get_recommendation_event_store().events_since(window_start)
        data_source = "memory_fallback"

    recommendation_import = _collect_import_metrics(rows)
    auto_materialization = _collect_auto_materialization_metrics(rows)
    processing = _collect_processing_metrics(db, completed_video_ids=recommendation_import["completed_video_ids"])
    recommendation_import.pop("completed_video_ids", None)

    return {
        "data_source": data_source,
        "window_days": days,
        "window_start": window_start,
        "window_end": window_end,
        "recommendation_import": recommendation_import,
        "auto_materialization": auto_materialization,
        "processing": processing,
    }
