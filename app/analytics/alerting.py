"""最小告警规则：失败率、超时率、相似度分值漂移。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from app.core.config import settings


@dataclass(frozen=True)
class AlertingThresholds:
    max_failure_rate: float
    max_timeout_rate: float
    latency_timeout_ms: float
    drift_relative_threshold: float


def default_thresholds() -> AlertingThresholds:
    return AlertingThresholds(
        max_failure_rate=float(getattr(settings, "ANALYTICS_ALERT_MAX_FAILURE_RATE", 0.15)),
        max_timeout_rate=float(getattr(settings, "ANALYTICS_ALERT_MAX_TIMEOUT_RATE", 0.10)),
        latency_timeout_ms=float(getattr(settings, "ANALYTICS_ALERT_LATENCY_TIMEOUT_MS", 30_000.0)),
        drift_relative_threshold=float(getattr(settings, "ANALYTICS_ALERT_DRIFT_REL_THRESHOLD", 0.10)),
    )


class AnalyticsAlertEngine:
    """
    轻量滑动窗口：按 (module, status, latency_ms) 观察，触发可读告警字符串。
    不依赖外部存储；仅进程内近似。同键告警受最小间隔节流，避免高流量重复 WARNING。
    """

    def __init__(
        self,
        thresholds: Optional[AlertingThresholds] = None,
        window_size: int = 500,
        min_interval_sec: Optional[float] = None,
    ):
        self.thresholds = thresholds or default_thresholds()
        self._window_size = max(50, window_size)
        self._rows: Deque[Tuple[str, str, Optional[float]]] = deque(maxlen=self._window_size)
        self._min_interval = float(
            min_interval_sec
            if min_interval_sec is not None
            else getattr(settings, "ANALYTICS_ALERT_MIN_INTERVAL_SEC", 60.0)
        )
        self._last_alert_at: Dict[str, float] = {}

    def observe(self, module: str, status: str, latency_ms: Optional[float]) -> None:
        self._rows.append((module, status, latency_ms))

    def _append_throttled(self, key: str, message: str, alerts: List[str]) -> None:
        now = time.monotonic()
        last = self._last_alert_at.get(key)
        if last is not None and (now - last) < self._min_interval:
            return
        self._last_alert_at[key] = now
        alerts.append(message)

    def evaluate_rates(self, module: str) -> List[str]:
        """对指定 module 计算失败率 / 超时率告警。"""
        rows = [r for r in self._rows if r[0] == module]
        if len(rows) < 20:
            return []

        alerts: List[str] = []
        n = len(rows)
        fail = sum(1 for _, st, _ in rows if st in ("error",))
        to = sum(1 for _, st, _ in rows if st == "timeout")
        slow = sum(1 for _, st, lat in rows if lat is not None and lat > self.thresholds.latency_timeout_ms)

        fail_rate = fail / n
        if fail_rate > self.thresholds.max_failure_rate:
            self._append_throttled(
                f"failure_rate:{module}",
                f"analytics_alert failure_rate module={module} rate={fail_rate:.3f} "
                f"threshold={self.thresholds.max_failure_rate:.3f} window={n}",
                alerts,
            )

        timeout_rate = max(to / n, slow / n)
        if timeout_rate > self.thresholds.max_timeout_rate:
            self._append_throttled(
                f"timeout_or_slow:{module}",
                f"analytics_alert timeout_or_slow module={module} rate={timeout_rate:.3f} "
                f"threshold={self.thresholds.max_timeout_rate:.3f} latency_cap_ms="
                f"{self.thresholds.latency_timeout_ms:.0f} window={n}",
                alerts,
            )

        return alerts

    @staticmethod
    def evaluate_drift_report(drift_report: dict) -> Optional[str]:
        """
        使用 SimilarityMetrics.check_drift 风格报告（含 drift_detected、drift_pct、threshold）。
        """
        if not drift_report.get("drift_detected"):
            return None
        thr = default_thresholds().drift_relative_threshold
        return (
            f"analytics_alert score_drift detected date={drift_report.get('date')} "
            f"drift_pct={drift_report.get('drift_pct')} threshold={thr}"
        )
