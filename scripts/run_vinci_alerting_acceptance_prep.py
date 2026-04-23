from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.vinci_alerting_acceptance import (
    AlertPlatformConfig,
    build_acceptance_commands,
    build_degraded_payload,
    validate_required_fields,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Vinci alerting acceptance payload and commands.")
    parser.add_argument("--grafana-url", default=os.getenv("GRAFANA_URL", ""))
    parser.add_argument("--loki-url", default=os.getenv("LOKI_URL", ""))
    parser.add_argument("--grafana-user", default=os.getenv("GRAFANA_USER", ""))
    parser.add_argument("--grafana-password", default=os.getenv("GRAFANA_PASSWORD", ""))
    parser.add_argument("--grafana-token", default=os.getenv("GRAFANA_TOKEN", ""))
    parser.add_argument("--event-count", type=int, default=40)
    parser.add_argument("--trace-prefix", default="vinci-preprod-drill")
    parser.add_argument("--output-dir", default="docs/monitoring/evidence/m3")
    args = parser.parse_args()

    config = AlertPlatformConfig(
        grafana_url=args.grafana_url,
        loki_url=args.loki_url,
        grafana_user=args.grafana_user,
        grafana_password=args.grafana_password,
        grafana_token=args.grafana_token,
    )
    missing = validate_required_fields(config)
    if missing:
        print(f"Missing required fields: {', '.join(missing)}")
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = build_degraded_payload(event_count=args.event_count, trace_prefix=args.trace_prefix)
    payload_path = output_dir / "vinci_degraded_drill_payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    commands = build_acceptance_commands(config=config, payload_path=str(payload_path))
    command_path = output_dir / "vinci_alerting_acceptance_commands.sh"
    command_path.write_text("\n".join(commands) + "\n", encoding="utf-8")

    print(f"Payload written: {payload_path}")
    print(f"Commands written: {command_path}")
    print("Run commands manually in preprod/prod with proper network and auth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
