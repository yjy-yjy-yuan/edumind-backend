"""Vinci 告警规则模板与 Runbook 同步性测试。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "docs/monitoring/grafana_loki_vinci_alert_rules.yaml"
RUNBOOK_PATH = ROOT / "docs/VINCI_RUNBOOK.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_vinci_alert_rules_cover_runbook_thresholds():
    """规则模板至少覆盖错误率/超时率/P95/降级突增四类阈值。"""
    rules_text = _read(RULES_PATH)
    runbook_text = _read(RUNBOOK_PATH)

    # Runbook 阈值声明（告警建议）
    assert "错误率连续 5 分钟 > 15% 报警" in runbook_text
    assert "超时率连续 5 分钟 > 10% 报警" in runbook_text
    assert "P95 连续 5 分钟 > 12s 报警" in runbook_text
    assert "10 分钟窗口超过 20 次时报警" in runbook_text

    # 规则模板覆盖四类阈值
    assert "uid: vinci-error-rate-high" in rules_text
    assert "summary: Vinci error rate > 15%" in rules_text

    assert "uid: vinci-timeout-rate-high" in rules_text
    assert "summary: Vinci timeout rate > 10%" in rules_text

    assert "uid: vinci-p95-latency-high" in rules_text
    assert "summary: Vinci p95 latency > 12s" in rules_text
    assert "quantile_over_time(0.95" in rules_text
    assert "| unwrap latency_ms [5m]" in rules_text

    assert "uid: vinci-degraded-burst" in rules_text
    assert "summary: Vinci degraded count > 20 in 10m" in rules_text
    assert '"status": "degraded"' in rules_text or '\\"status\\": \\"degraded\\"' in rules_text
    assert "> 20" in rules_text
