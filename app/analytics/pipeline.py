"""统一遥测写入管道（结构化 JSON 日志 + 可选告警）。"""

from __future__ import annotations

import json
import logging
from typing import Any
from typing import Dict
from typing import Optional

from app.analytics.alerting import AnalyticsAlertEngine
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import validate_analytics_event

_LOG = logging.getLogger("app.analytics.telemetry")


def _configure_logger_level() -> None:
    try:
        from app.core.config import settings

        lvl = getattr(logging, (getattr(settings, "ANALYTICS_LOG_LEVEL", "INFO") or "INFO").upper(), logging.INFO)
        _LOG.setLevel(lvl)
    except Exception:
        _LOG.setLevel(logging.INFO)


def _level_for_status(status: str) -> int:
    if status in ("error",):
        return logging.ERROR
    if status in ("timeout", "degraded"):
        return logging.WARNING
    return logging.INFO


class AnalyticsTelemetry:
    """
    集中式写入入口：校验 schema → JSON 行日志 → 告警引擎观察。

    日志级别随 status 变化；logger 有效级别由 settings.ANALYTICS_LOG_LEVEL 控制。
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._logger = logger or _LOG
        self._alerts = AnalyticsAlertEngine()

    def emit(self, event: AnalyticsEvent, *, skip_alerts: bool = False) -> None:
        validate_analytics_event(event)
        payload = event.to_envelope()
        line = json.dumps(payload, ensure_ascii=False, default=str)
        self._logger.log(_level_for_status(event.status), line)
        if not skip_alerts:
            self._alerts.observe(event.module, event.status, event.latency_ms)
            for msg in self._alerts.evaluate_rates(event.module):
                self._logger.warning(msg)

    def emit_dict(self, payload: Dict[str, Any], *, skip_alerts: bool = False) -> None:
        """供适配器从遗留 dict 构造事件后写入（需已符合 AnalyticsEvent 字段）。"""
        ev = AnalyticsEvent(
            event_type=str(payload["event_type"]),
            trace_id=str(payload["trace_id"]),
            module=str(payload["module"]),
            status=str(payload["status"]),
            latency_ms=payload.get("latency_ms"),
            metadata=dict(payload.get("metadata") or {}),
        )
        self.emit(ev, skip_alerts=skip_alerts)


_telemetry: Optional[AnalyticsTelemetry] = None


def get_telemetry() -> AnalyticsTelemetry:
    global _telemetry
    if _telemetry is None:
        _configure_logger_level()
        _telemetry = AnalyticsTelemetry()
    return _telemetry


def reset_telemetry_for_tests() -> None:
    """测试用：重置单例。"""
    global _telemetry
    _telemetry = None
