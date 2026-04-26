"""Compatibility facade for centralized analytics service.

The canonical implementation lives in ``app.analytics``.
This package keeps a stable ``app.services.analytics`` entrypoint for
deployment checklists and external integrations.
"""

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
    "AnalyticsTelemetry",
    "AnalyticsEvent",
    "AnalyticsStatus",
    "get_telemetry",
    "reset_telemetry_for_tests",
    "validate_analytics_event",
]
