from __future__ import annotations

from app.services.llm_clients.vinci_alerting_acceptance import (
    AlertPlatformConfig,
    build_acceptance_commands,
    build_degraded_payload,
    validate_required_fields,
)


def test_validate_required_fields_detects_missing_urls_and_auth():
    config = AlertPlatformConfig(
        grafana_url="",
        loki_url="",
        grafana_user="",
        grafana_password="",
        grafana_token="",
    )
    missing = validate_required_fields(config)
    assert "grafana_url" in missing
    assert "loki_url" in missing
    assert "grafana_auth" in missing


def test_build_degraded_payload_generates_expected_count_and_trace_prefix():
    payload = build_degraded_payload(event_count=7, trace_prefix="m3-prod-drill")
    streams = payload.get("streams", [])
    assert len(streams) == 1
    values = streams[0]["values"]
    assert len(values) == 7
    assert "m3-prod-drill-01" in values[0][1]
    assert '"status": "degraded"' in values[0][1]


def test_build_acceptance_commands_contains_import_drill_and_verify_paths():
    config = AlertPlatformConfig(
        grafana_url="https://grafana.pre.example.com",
        loki_url="https://loki.pre.example.com",
        grafana_user="admin",
        grafana_password="secret",
        grafana_token="",
    )
    commands = build_acceptance_commands(
        config=config,
        payload_path="docs/monitoring/evidence/m3/preprod_payload.json",
    )
    script = "\n".join(commands)
    assert "/api/health" in script
    assert "/api/v1/provisioning/alert-rules" in script
    assert "/loki/api/v1/push" in script
    assert "/api/alertmanager/grafana/api/v2/alerts" in script
    assert "preprod_payload.json" in script
    assert "${GRAFANA_USER}" in script
    assert "${GRAFANA_PASSWORD}" in script
    assert "secret" not in script
