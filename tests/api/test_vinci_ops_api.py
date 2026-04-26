"""Vinci 观测 API 测试。"""

from __future__ import annotations

import pytest

from app.analytics.pipeline import get_telemetry, reset_telemetry_for_tests
from app.analytics.schema import AnalyticsEvent, AnalyticsStatus
from app.utils.auth_token import build_auth_token


@pytest.fixture(autouse=True)
def _reset_telemetry():
    reset_telemetry_for_tests()
    yield
    reset_telemetry_for_tests()


@pytest.mark.api
def test_get_vinci_ops_metrics_requires_bearer(client):
    response = client.get("/api/ops/vinci/metrics")
    assert response.status_code == 401


@pytest.mark.api
def test_get_vinci_ops_metrics_returns_module_metrics(client, sample_user):
    telemetry = get_telemetry()
    for _ in range(8):
        telemetry.emit(
            AnalyticsEvent(
                event_type="vinci_ok",
                trace_id="ops-metric-ok",
                module="vinci",
                status=AnalyticsStatus.OK.value,
                latency_ms=120.0,
            ),
            skip_alerts=False,
        )
    for _ in range(2):
        telemetry.emit(
            AnalyticsEvent(
                event_type="vinci_degraded",
                trace_id="ops-metric-degraded",
                module="vinci",
                status=AnalyticsStatus.DEGRADED.value,
                latency_ms=450.0,
            ),
            skip_alerts=False,
        )

    response = client.get(
        "/api/ops/vinci/metrics",
        headers={
            "Authorization": f"Bearer {build_auth_token(sample_user.id)}",
            "X-Trace-Id": "trace-vinci-ops-1",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("X-Trace-Id") == "trace-vinci-ops-1"
    payload = response.json()
    assert payload["module"] == "vinci"
    assert payload["total"] == 10
    assert payload["degraded_count"] == 2
    assert payload["success_count"] == 8
    assert "thresholds" in payload
    assert "max_p95_latency_ms" in payload["thresholds"]
