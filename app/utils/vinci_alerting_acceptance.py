from __future__ import annotations

from dataclasses import dataclass
from time import time_ns
from typing import Dict, List


@dataclass(frozen=True)
class AlertPlatformConfig:
    grafana_url: str
    loki_url: str
    grafana_user: str = ""
    grafana_password: str = ""
    grafana_token: str = ""


def validate_required_fields(config: AlertPlatformConfig) -> List[str]:
    missing: List[str] = []
    if not config.grafana_url.strip():
        missing.append("grafana_url")
    if not config.loki_url.strip():
        missing.append("loki_url")
    if not config.grafana_token.strip() and not (config.grafana_user.strip() and config.grafana_password.strip()):
        missing.append("grafana_auth")
    return missing


def build_degraded_payload(
    *,
    event_count: int,
    trace_prefix: str = "vinci-drill",
    latency_ms: int = 15000,
) -> Dict[str, object]:
    values: List[List[str]] = []
    for idx in range(1, max(0, int(event_count)) + 1):
        trace_id = f"{trace_prefix}-{idx:02d}"
        message = (
            'app.analytics.telemetry {"module": "vinci", "status": "degraded", '
            f'"latency_ms": {int(latency_ms)}, "trace_id": "{trace_id}"}}'
        )
        values.append([str(time_ns()), message])
    return {
        "streams": [
            {
                "stream": {"app": "edumind-backend", "module": "vinci", "source": "vinci-alert-drill"},
                "values": values,
            }
        ]
    }


def _build_auth_curl_fragment(config: AlertPlatformConfig) -> str:
    if config.grafana_token.strip():
        return "-H 'Authorization: Bearer ${GRAFANA_TOKEN}'"
    return '-u "${GRAFANA_USER}:${GRAFANA_PASSWORD}"'


def build_acceptance_commands(*, config: AlertPlatformConfig, payload_path: str) -> List[str]:
    grafana_url = config.grafana_url.rstrip("/")
    loki_url = config.loki_url.rstrip("/")
    auth = _build_auth_curl_fragment(config)

    return [
        f"curl -sS {auth} {grafana_url}/api/health",
        f"curl -sS {auth} {grafana_url}/api/v1/provisioning/alert-rules",
        (
            f"curl -sS -X POST {loki_url}/loki/api/v1/push "
            f"-H 'Content-Type: application/json' --data-binary '@{payload_path}'"
        ),
        f"curl -sS {auth} {grafana_url}/api/prometheus/grafana/api/v1/rules",
        f"curl -sS {auth} {grafana_url}/api/alertmanager/grafana/api/v2/alerts",
    ]
