"""Backward-compatible analytics schema import surface."""

from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus
from app.analytics.schema import validate_analytics_event

__all__ = ["AnalyticsEvent", "AnalyticsStatus", "validate_analytics_event"]
