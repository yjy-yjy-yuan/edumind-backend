curl -sS -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" https://grafana.pre.example.com/api/health
curl -sS -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" https://grafana.pre.example.com/api/v1/provisioning/alert-rules
curl -sS -X POST https://loki.pre.example.com/loki/api/v1/push -H 'Content-Type: application/json' --data-binary '@docs/monitoring/evidence/m3/preprod_prep/vinci_degraded_drill_payload.json'
curl -sS -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" https://grafana.pre.example.com/api/prometheus/grafana/api/v1/rules
curl -sS -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" https://grafana.pre.example.com/api/alertmanager/grafana/api/v2/alerts
