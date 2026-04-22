"""统一遥测管道写入与聚合占位测试。"""

import json
import logging

import pytest
from app.analytics.pipeline import AnalyticsTelemetry
from app.analytics.pipeline import reset_telemetry_for_tests
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus


@pytest.fixture(autouse=True)
def _reset_telemetry():
    reset_telemetry_for_tests()
    yield
    reset_telemetry_for_tests()


class TestAnalyticsPipeline:
    def test_emit_success(self, caplog):
        caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
        t = AnalyticsTelemetry()
        ev = AnalyticsEvent(
            event_type="unit_ping",
            trace_id="t1",
            module="search",
            status=AnalyticsStatus.OK.value,
            latency_ms=1.0,
            metadata={"k": "v"},
        )
        t.emit(ev, skip_alerts=True)
        lines = [r.message for r in caplog.records if r.name == "app.analytics.telemetry"]
        assert lines
        payload = json.loads(lines[0])
        assert payload["event_type"] == "unit_ping"
        assert payload["module"] == "search"
        assert payload["status"] == "ok"

    def test_emit_error_level(self, caplog):
        caplog.set_level(logging.ERROR, logger="app.analytics.telemetry")
        t = AnalyticsTelemetry()
        ev = AnalyticsEvent(
            event_type="unit_fail",
            trace_id="t2",
            module="similarity",
            status=AnalyticsStatus.ERROR.value,
            metadata={},
        )
        t.emit(ev, skip_alerts=True)
        assert any(r.levelno == logging.ERROR for r in caplog.records)
