"""告警规则（失败率 / 超时率 / 漂移）。"""

from app.analytics.alerting import AlertingThresholds
from app.analytics.alerting import AnalyticsAlertEngine


class TestAnalyticsAlerting:
    def test_failure_rate_alert(self):
        eng = AnalyticsAlertEngine(
            thresholds=AlertingThresholds(
                max_failure_rate=0.2,
                max_timeout_rate=0.5,
                latency_timeout_ms=1000.0,
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
