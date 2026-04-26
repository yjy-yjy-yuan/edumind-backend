"""集中式遥测与分析管道（P1-2）。"""

from app.analytics.pipeline import (
    AnalyticsTelemetry,
    get_telemetry,
    reset_telemetry_for_tests,
)
from app.analytics.schema import (
    AnalyticsEvent,
    AnalyticsStatus,
    validate_analytics_event,
)

__all__ = [
    "AnalyticsEvent",
    "AnalyticsStatus",
    "AnalyticsTelemetry",
    "get_telemetry",
    "reset_telemetry_for_tests",
    "validate_analytics_event",
]
