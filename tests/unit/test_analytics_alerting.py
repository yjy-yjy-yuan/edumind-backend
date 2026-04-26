"""告警规则（失败率 / 超时率 / 漂移）。"""

from app.analytics.alerting import AlertingThresholds, AnalyticsAlertEngine


class TestAnalyticsAlerting:
    def test_failure_rate_alert(self):
        eng = AnalyticsAlertEngine(
            thresholds=AlertingThresholds(
                max_failure_rate=0.2,
                max_timeout_rate=0.5,
                latency_timeout_ms=1000.0,
                max_p95_latency_ms=1500.0,
                drift_relative_threshold=0.1,
            ),
            window_size=50,
            min_interval_sec=0.0,
        )
        for i in range(25):
            eng.observe("search", "error" if i < 15 else "ok", 10.0)
        msgs = eng.evaluate_rates("search")
        assert msgs and any("failure_rate" in m for m in msgs)

    def test_rate_alerts_throttled_on_second_evaluate(self):
        eng = AnalyticsAlertEngine(
            thresholds=AlertingThresholds(
                max_failure_rate=0.2,
                max_timeout_rate=0.5,
                latency_timeout_ms=1000.0,
                max_p95_latency_ms=1500.0,
                drift_relative_threshold=0.1,
            ),
            window_size=50,
            min_interval_sec=3600.0,
        )
        for i in range(25):
            eng.observe("search", "error" if i < 15 else "ok", 10.0)
        msgs1 = eng.evaluate_rates("search")
        msgs2 = eng.evaluate_rates("search")
        assert msgs1
        assert msgs2 == []

    def test_drift_report_message(self):
        rep = {
            "date": "2026-04-10",
            "drift_detected": True,
            "drift_pct": 0.5,
        }
        msg = AnalyticsAlertEngine.evaluate_drift_report(rep)
        assert msg and "score_drift" in msg

    def test_drift_report_no_alert_when_false(self):
        assert AnalyticsAlertEngine.evaluate_drift_report({"drift_detected": False}) is None

    def test_module_metrics_include_success_error_timeout_p95_and_degraded_count(self):
        eng = AnalyticsAlertEngine(
            thresholds=AlertingThresholds(
                max_failure_rate=0.5,
                max_timeout_rate=0.5,
                latency_timeout_ms=1000.0,
                max_p95_latency_ms=1500.0,
                drift_relative_threshold=0.1,
            ),
            window_size=200,
            min_interval_sec=0.0,
        )
        for _ in range(40):
            eng.observe("vinci", "ok", 100.0)
        for _ in range(5):
            eng.observe("vinci", "error", 400.0)
        for _ in range(3):
            eng.observe("vinci", "timeout", 1400.0)
        for _ in range(2):
            eng.observe("vinci", "degraded", 300.0)

        metrics = eng.get_module_metrics("vinci")
        assert metrics["total"] == 50
        assert metrics["success_count"] == 40
        assert metrics["error_count"] == 5
        assert metrics["timeout_count"] == 3
        assert metrics["degraded_count"] == 2
        assert metrics["success_rate"] == 0.8
        assert metrics["error_rate"] == 0.1
        assert metrics["timeout_rate"] == 0.06
        assert metrics["p95_latency_ms"] is not None
        assert metrics["p95_latency_ms"] >= 100.0

    def test_p95_alert_emitted_when_threshold_exceeded(self):
        eng = AnalyticsAlertEngine(
            thresholds=AlertingThresholds(
                max_failure_rate=0.9,
                max_timeout_rate=0.9,
                latency_timeout_ms=200.0,
                drift_relative_threshold=0.1,
                max_p95_latency_ms=120.0,
            ),
            window_size=100,
            min_interval_sec=0.0,
        )
        for _ in range(30):
            eng.observe("vinci", "ok", 150.0)

        messages = eng.evaluate_rates("vinci")
        assert any("p95_latency" in msg for msg in messages)
