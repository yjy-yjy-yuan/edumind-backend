"""集中式遥测与分析管道（P1-2）。"""

from app.analytics.pipeline import AnalyticsTelemetry
from app.analytics.pipeline import get_telemetry
from app.analytics.pipeline import reset_telemetry_for_tests
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus
from app.analytics.schema import validate_analytics_event

__all__ = [
    "AnalyticsEvent",
    "AnalyticsStatus",
    "AnalyticsTelemetry",
    "get_telemetry",
    "reset_telemetry_for_tests",
    "validate_analytics_event",
]
