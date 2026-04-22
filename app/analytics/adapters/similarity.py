"""相似度模块：SimilarityAuditLog → 统一 AnalyticsEvent。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Dict

from app.analytics.pipeline import AnalyticsTelemetry
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus

if TYPE_CHECKING:
    from app.services.similarity_analytics import SimilarityAuditLog

_MODULE = "similarity"


def _status_from_audit(log: "SimilarityAuditLog") -> str:
    et = (log.event_type or "").lower()
    if "call_start" in et or "retry_start" in et:
        return AnalyticsStatus.STARTED.value
    if "fallback" in et:
        return AnalyticsStatus.DEGRADED.value
    if log.success:
        return AnalyticsStatus.OK.value
    return AnalyticsStatus.ERROR.value


def similarity_audit_to_event(log: "SimilarityAuditLog") -> AnalyticsEvent:
    base = log.to_dict()
    latency = float(log.latency_ms) if log.latency_ms is not None else None
    meta: Dict[str, Any] = {k: v for k, v in base.items() if k not in ("event_type", "trace_id", "latency_ms")}
    return AnalyticsEvent(
        event_type=str(log.event_type),
        trace_id=str(log.trace_id),
        module=_MODULE,
        status=_status_from_audit(log),
        latency_ms=latency,
        metadata=meta,
    )


def emit_similarity_audit_event(telemetry: AnalyticsTelemetry, log: "SimilarityAuditLog") -> None:
    telemetry.emit(similarity_audit_to_event(log))
