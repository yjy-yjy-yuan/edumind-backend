"""Sleek API 服务封装。"""

from __future__ import annotations

import time
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Optional

import httpx
from app.core.config import settings


class SleekConfigError(RuntimeError):
    """Sleek 未配置或配置不完整。"""


class SleekAPIError(RuntimeError):
    """Sleek API 调用失败。"""

    def __init__(self, message: str, *, status_code: int = 502, error_code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def is_configured() -> bool:
    """是否已配置 Sleek API Key。"""
    return bool(str(settings.SLEEK_API_KEY or "").strip())


def _base_url() -> str:
    return str(settings.SLEEK_API_BASE or "https://sleek.design").rstrip("/")


def _headers(idempotency_key: Optional[str] = None) -> Dict[str, str]:
    api_key = str(settings.SLEEK_API_KEY or "").strip()
    if not api_key:
        raise SleekConfigError("未配置 SLEEK_API_KEY，无法启用 Sleek 设计能力。")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key[:255]
    return headers


def _extract_error_payload(response: httpx.Response) -> tuple[str, str]:
    default_message = f"Sleek 请求失败（HTTP {response.status_code}）"
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or default_message, ""

    error_obj = payload.get("error")
    if isinstance(error_obj, dict):
        message = str(error_obj.get("message") or "").strip()
        code = str(error_obj.get("code") or "").strip()
        if message:
            return message, code

    message = str(payload.get("message") or payload.get("detail") or payload.get("error") or "").strip()
    code = str(payload.get("code") or "").strip()
    return (message or default_message), code


def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[dict[str, Any]] = None,
    expect_json: bool = True,
    idempotency_key: Optional[str] = None,
) -> Any:
    url = f"{_base_url()}{path}"
    timeout = httpx.Timeout(60.0, connect=15.0)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(
                method,
                url,
                params=params,
                json=json,
                headers=_headers(idempotency_key=idempotency_key),
            )
    except httpx.RequestError as exc:
        raise SleekAPIError(f"Sleek 网络请求失败：{exc}") from exc

    if response.status_code >= 400:
        message, error_code = _extract_error_payload(response)
        raise SleekAPIError(message, status_code=response.status_code, error_code=error_code)

    if not expect_json:
        return {
            "content": response.content,
            "content_type": response.headers.get("content-type", "application/octet-stream"),
        }

    try:
        return response.json()
    except ValueError as exc:
        raise SleekAPIError("Sleek 返回了无法解析的 JSON 响应。") from exc


def list_projects(*, limit: Optional[int] = None, offset: int = 0) -> dict[str, Any]:
    project_limit = int(limit or settings.SLEEK_PROJECT_LIMIT or 50)
    return _request(
        "GET", "/api/v1/projects", params={"limit": max(1, min(project_limit, 100)), "offset": max(0, offset)}
    )


def create_project(name: str) -> dict[str, Any]:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise SleekAPIError("项目名称不能为空。", status_code=400, error_code="BAD_REQUEST")
    return _request("POST", "/api/v1/projects", json={"name": normalized_name})


def list_components(project_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return _request(
        "GET",
        f"/api/v1/projects/{project_id}/components",
        params={"limit": max(1, min(int(limit), 100)), "offset": max(0, int(offset))},
    )


def get_component(project_id: str, component_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/v1/projects/{project_id}/components/{component_id}")


def create_chat_run(
    project_id: str,
    *,
    message_text: str,
    image_urls: Optional[Iterable[str]] = None,
    target_screen_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    normalized_message = str(message_text or "").strip()
    if not normalized_message:
        raise SleekAPIError("设计描述不能为空。", status_code=400, error_code="BAD_REQUEST")

    payload: dict[str, Any] = {
        "message": {"text": normalized_message},
    }
    urls = [str(item).strip() for item in (image_urls or []) if str(item).strip()]
    if urls:
        payload["imageUrls"] = urls
    if target_screen_id:
        payload["target"] = {"screenId": str(target_screen_id).strip()}

    return _request(
        "POST",
        f"/api/v1/projects/{project_id}/chat/messages",
        params={"wait": "false"},
        json=payload,
        idempotency_key=idempotency_key,
    )


def get_chat_run(project_id: str, run_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/v1/projects/{project_id}/chat/runs/{run_id}")


def wait_for_chat_run(project_id: str, run_id: str, *, timeout_seconds: Optional[int] = None) -> dict[str, Any]:
    timeout = max(1, int(timeout_seconds or settings.SLEEK_POLL_TIMEOUT_SECONDS or 300))
    initial_interval = max(1, int(settings.SLEEK_POLL_INITIAL_INTERVAL_SECONDS or 2))
    backoff_after = max(1, int(settings.SLEEK_POLL_BACKOFF_AFTER_SECONDS or 10))
    backoff_interval = max(initial_interval, int(settings.SLEEK_POLL_BACKOFF_INTERVAL_SECONDS or 5))

    start = time.monotonic()
    last_payload = get_chat_run(project_id, run_id)

    while True:
        data = last_payload.get("data") if isinstance(last_payload, dict) else {}
        status = str((data or {}).get("status") or "").strip().lower()
        if status in {"completed", "failed"}:
            return last_payload

        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            return last_payload

        time.sleep(backoff_interval if elapsed >= backoff_after else initial_interval)
        last_payload = get_chat_run(project_id, run_id)


def render_screenshot(
    project_id: str,
    component_ids: Iterable[str],
    *,
    image_format: str = "png",
    scale: int = 2,
    background: str = "transparent",
    show_dots: bool = False,
) -> dict[str, Any]:
    normalized_component_ids = [str(item).strip() for item in component_ids if str(item).strip()]
    if not normalized_component_ids:
        raise SleekAPIError("截图组件不能为空。", status_code=400, error_code="BAD_REQUEST")

    payload = {
        "componentIds": normalized_component_ids,
        "projectId": project_id,
        "format": "webp" if str(image_format).lower() == "webp" else "png",
        "scale": max(1, min(int(scale), 3)),
        "background": str(background or "transparent").strip() or "transparent",
        "showDots": bool(show_dots),
    }
    return _request("POST", "/api/v1/screenshots", json=payload, expect_json=False)
