#!/usr/bin/env python3
"""Verify local/cloud isolation without blocking application startup.

This script is intentionally a diagnostic guardrail, not runtime business logic.
Run it before local debugging to ensure local env files do not point to cloud
database/API/Vinci targets.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ENV = ROOT / ".env"
FRONTEND_ENV = ROOT.parent / "EduMind" / "mobile-frontend" / ".env"
CLOUD_MARKERS = {"47.84.228.226"}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        values[key.strip()] = val.strip()
    return values


def is_loopback_or_private_host(host: Optional[str]) -> bool:
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def is_local_host(host: Optional[str]) -> bool:
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return ip.is_loopback


def extract_hostname(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text if "://" in text else f"//{text}")
    return parsed.hostname


def is_local_database(database_url: str) -> bool:
    if database_url.startswith("sqlite"):
        return True
    return is_loopback_or_private_host(extract_hostname(database_url))


def contains_cloud_marker(value: str) -> bool:
    text = (value or "").lower()
    return any(marker in text for marker in CLOUD_MARKERS)


def validate_backend(env: dict[str, str]) -> tuple[list[dict], list[dict]]:
    checks: list[dict] = []
    failures: list[dict] = []

    app_env = env.get("APP_ENV", "")
    database_url = env.get("DATABASE_URL", "")
    vinci_url = env.get("VINCI_BASE_URL", "")

    def push(name: str, ok: bool, detail: str) -> None:
        payload = {"name": name, "ok": ok, "detail": detail}
        checks.append(payload)
        if not ok:
            failures.append(payload)

    push(
        "app_env_local",
        app_env.lower() in {"local", "development"},
        f"APP_ENV={app_env!r}",
    )
    push(
        "database_local_only",
        is_local_database(database_url),
        f"DATABASE_URL={database_url!r}",
    )
    vinci_host = extract_hostname(vinci_url)
    push(
        "vinci_local_only",
        is_local_host(vinci_host),
        f"VINCI_BASE_URL={vinci_url!r}",
    )
    push(
        "backend_env_no_cloud_marker",
        not any(contains_cloud_marker(v) for v in env.values()),
        "backend .env contains no known cloud marker",
    )
    return checks, failures


def validate_frontend(env: dict[str, str]) -> tuple[list[dict], list[dict]]:
    checks: list[dict] = []
    failures: list[dict] = []

    def push(name: str, ok: bool, detail: str) -> None:
        payload = {"name": name, "ok": ok, "detail": detail}
        checks.append(payload)
        if not ok:
            failures.append(payload)

    api_base = env.get("VITE_MOBILE_API_BASE_URL", "")
    proxy_target = env.get("VITE_MOBILE_PROXY_TARGET", "")

    if not env:
        push("frontend_env_present", False, f"frontend env not found: {FRONTEND_ENV}")
        return checks, failures

    for key, value in [
        ("frontend_api_base_local_only", api_base),
        ("frontend_proxy_target_local_only", proxy_target),
    ]:
        host = extract_hostname(value)
        push(key, is_local_host(host), f"{key}={value!r}")

    push(
        "frontend_env_no_cloud_marker",
        not any(contains_cloud_marker(v) for v in env.values()),
        "frontend .env contains no known cloud marker",
    )
    return checks, failures


def main() -> int:
    backend_env = read_env(BACKEND_ENV)
    frontend_env = read_env(FRONTEND_ENV)

    backend_checks, backend_failures = validate_backend(backend_env)
    frontend_checks, frontend_failures = validate_frontend(frontend_env)
    failures = backend_failures + frontend_failures

    report = {
        "ok": len(failures) == 0,
        "backend_env_file": str(BACKEND_ENV),
        "frontend_env_file": str(FRONTEND_ENV),
        "checks": backend_checks + frontend_checks,
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
