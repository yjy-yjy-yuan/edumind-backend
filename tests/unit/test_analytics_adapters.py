"""适配器：search / similarity 遗留结构映射。"""

import pytest
from app.analytics.adapters.search import legacy_search_dict_to_event
from app.analytics.pipeline import AnalyticsTelemetry
from app.analytics.pipeline import reset_telemetry_for_tests
from app.analytics.schema import validate_analytics_event
from app.services.similarity_analytics import SimilarityAuditLog
from app.services.similarity_analytics import SimilarityEventType


@pytest.fixture(autouse=True)
def _reset():
    reset_telemetry_for_tests()
    yield
    reset_telemetry_for_tests()


class TestSearchAdapter:
    def test_legacy_maps_module_and_status(self):
        legacy = {
            "event": "search_completed",
            "timestamp": "x",
            "user_id": 1,
            "duration_ms": 42.0,
            "result_count": 3,
        }
        ev = legacy_search_dict_to_event(legacy)
        assert ev.module == "search"
        assert ev.status == "ok"
        assert ev.latency_ms == 42.0
        assert ev.metadata.get("result_count") == 3
        assert ev.trace_id == "unset"
        assert ev.metadata.get("trace_id_source") == "missing"

    def test_upstream_trace_id(self):
        legacy = {
            "event": "search_completed",
            "trace_id": "upstream-trace-1",
            "duration_ms": 1.0,
        }
        ev = legacy_search_dict_to_event(legacy)
        assert ev.trace_id == "upstream-trace-1"
        assert ev.metadata.get("trace_id_source") == "upstream"

    def test_duration_parse_error_keeps_event(self):
        legacy = {"event": "search_completed", "duration_ms": "not-a-number"}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.latency_ms is None
        assert "parse_error" in ev.metadata
        assert "invalid" in ev.metadata["parse_error"].lower()

    def test_invalid_duration_bool(self):
        legacy = {"event": "search_completed", "duration_ms": True}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.latency_ms is None
        assert "parse_error" in ev.metadata

    def test_duration_string_nan_keeps_event(self):
        legacy = {"event": "search_completed", "duration_ms": "nan"}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.latency_ms is None
        assert "parse_error" in ev.metadata
        assert "non-finite" in ev.metadata["parse_error"].lower()
        validate_analytics_event(ev)

    def test_duration_string_inf_keeps_event(self):
        legacy = {"event": "search_completed", "duration_ms": "inf"}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.latency_ms is None
        assert "parse_error" in ev.metadata
        validate_analytics_event(ev)

    def test_duration_negative_string_keeps_event(self):
        legacy = {"event": "search_completed", "duration_ms": "-1"}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.latency_ms is None
        assert "parse_error" in ev.metadata
        assert "negative" in ev.metadata["parse_error"].lower()
        validate_analytics_event(ev)

    def test_duration_exceeds_max_keeps_event(self):
        legacy = {"event": "search_completed", "duration_ms": "1e13"}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.latency_ms is None
        assert "parse_error" in ev.metadata
        assert "exceeds maximum" in ev.metadata["parse_error"].lower()
        validate_analytics_event(ev)

    def test_timeout_keyword_status(self):
        ev = legacy_search_dict_to_event({"event": "chromadb_search_timeout"})
        assert ev.status == "timeout"

    def test_degraded_keyword_status(self):
        ev = legacy_search_dict_to_event({"event": "search_partial_fallback"})
        assert ev.status == "degraded"

    def test_failed_event_status(self):
        legacy = {"event": "search_failed", "user_id": 1}
        ev = legacy_search_dict_to_event(legacy)
        assert ev.status == "error"


class TestSimilarityAdapter:
    def test_success_audit(self):
        from app.analytics.adapters.similarity import similarity_audit_to_event

        log = SimilarityAuditLog(
            trace_id="abc",
            event_type=SimilarityEventType.CALL_SUCCESS.value,
            tag1="a",
            tag2="b",
        )
        log.success = True
        log.latency_ms = 10.0
        ev = similarity_audit_to_event(log)
        assert ev.module == "similarity"
        assert ev.status == "ok"

    def test_emit_roundtrip(self, caplog):
        import logging

        from app.analytics.adapters.similarity import emit_similarity_audit_event

        caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
        log = SimilarityAuditLog(
            event_type=SimilarityEventType.CALL_SUCCESS.value,
            tag1="a",
            tag2="b",
        )
        log.success = True
        log.latency_ms = 5.0
        emit_similarity_audit_event(AnalyticsTelemetry(), log)
        assert any("similarity_call_success" in r.message for r in caplog.records)
