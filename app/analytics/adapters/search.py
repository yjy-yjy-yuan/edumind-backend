"""搜索模块：遗留 SearchEventLogger dict → 统一 AnalyticsEvent。"""

from __future__ import annotations

import math
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from app.analytics.pipeline import AnalyticsTelemetry
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus
from app.core.config import settings

_MODULE = "search"


def _finalize_duration_value(v: float) -> Tuple[Optional[float], Optional[str]]:
    """有限、非负、上限内则接受；否则整条事件仍发，latency 置空并带原因。"""
    if math.isnan(v) or math.isinf(v):
        return None, "duration_ms: non-finite float"
    if v < 0:
        return None, "duration_ms: negative not allowed"
    if v > 1e12:
        return None, "duration_ms: exceeds maximum (1e12 ms)"
    return v, None


def _parse_duration_ms(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    """
    容错解析 duration_ms；失败时返回 (None, 错误说明)，成功时第二项为 None。
    错误说明写入 metadata.parse_error，事件仍可落库。
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, "duration_ms: boolean not allowed"
    if isinstance(raw, (int, float)):
        return _finalize_duration_value(float(raw))
    s = str(raw).strip().replace(",", "")
    if not s:
        return None, "duration_ms: empty string"
    try:
        v = float(s)
    except ValueError:
        return None, f"duration_ms: invalid value {raw!r}"
    return _finalize_duration_value(v)


def _infer_status(event_name: str) -> str:
    """按事件名关键字推断 status（顺序：timeout → degraded → error → started → ok）。"""
    name = (event_name or "").lower()
    if any(k in name for k in ("timeout", "timed_out", "time_out")):
        return AnalyticsStatus.TIMEOUT.value
    if any(k in name for k in ("degraded", "partial_success", "fallback")):
        return AnalyticsStatus.DEGRADED.value
    if any(k in name for k in ("failed", "_fail", "fault", "_error")):
        return AnalyticsStatus.ERROR.value
    if name == "search_request_received" or name.endswith("_request_received"):
        return AnalyticsStatus.STARTED.value
    return AnalyticsStatus.OK.value


def _resolve_trace_id(legacy: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    优先使用上游 trace_id；缺省使用配置占位符并标注 trace_id_source。
    返回 (trace_id, extra_metadata)。
    """
    extra: Dict[str, Any] = {}
    raw = legacy.get("trace_id")
    if raw is not None and str(raw).strip():
        tid = str(raw).strip()[:128]
        extra["trace_id_source"] = "upstream"
        return tid, extra
    placeholder = getattr(settings, "ANALYTICS_TRACE_ID_PLACEHOLDER", "unset") or "unset"
    extra["trace_id_source"] = "missing"
    return str(placeholder)[:128], extra


def legacy_search_dict_to_event(legacy: Dict[str, Any]) -> AnalyticsEvent:
    """将 SearchEventLogger 产生的扁平 dict 转为 AnalyticsEvent。"""
    event_name = str(legacy.get("event") or "search_unknown")
    trace_id, trace_meta = _resolve_trace_id(legacy)
    latency_ms, dur_err = _parse_duration_ms(legacy.get("duration_ms"))

    skip = {"event", "timestamp", "trace_id", "duration_ms"}
    metadata = {k: v for k, v in legacy.items() if k not in skip and v is not None}
    metadata.update(trace_meta)
    if dur_err:
        metadata["parse_error"] = dur_err

    return AnalyticsEvent(
        event_type=event_name,
        trace_id=trace_id,
        module=_MODULE,
        status=_infer_status(event_name),
        latency_ms=latency_ms,
        metadata=metadata,
    )


def emit_search_legacy_event(telemetry: AnalyticsTelemetry, legacy: Dict[str, Any]) -> None:
    telemetry.emit(legacy_search_dict_to_event(legacy))
