"""Vinci 微服务客户端（HTTP / SSE）。"""

from __future__ import annotations

import json
from typing import Any, Generator, Iterable, Optional

import httpx

from app.core.config import settings


class VinciClientError(RuntimeError):
    """Vinci 客户端通用错误。"""


class VinciTimeoutError(VinciClientError):
    """Vinci 请求超时。"""


class VinciUnavailableError(VinciClientError):
    """Vinci 不可达或网络不可用。"""


class VinciHTTPError(VinciClientError):
    """Vinci 返回非 2xx。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        error_code: str = "",
        response_body: str = "",
    ):
        super().__init__(message)
        self.status_code = int(status_code or 502)
        self.error_code = str(error_code or "").strip()
        self.response_body = str(response_body or "")


def _normalize_base_url(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return "http://127.0.0.1:8010"
    return normalized.rstrip("/")


def _build_url(base_url: str, path: str) -> str:
    normalized_path = f"/{str(path or '').lstrip('/')}"
    return f"{_normalize_base_url(base_url)}{normalized_path}"


def _extract_error_message(response: httpx.Response) -> tuple[str, str]:
    default = f"vinci upstream error: http {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        text = str(response.text or "").strip()
        return text or default, ""

    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            message = str(error_obj.get("message") or "").strip()
            code = str(error_obj.get("code") or "").strip()
            if message:
                return message, code

        message = str(payload.get("message") or payload.get("detail") or payload.get("error") or "").strip()
        code = str(payload.get("code") or "").strip()
        if message:
            return message, code
    return default, ""


class VinciClient:
    """Vinci HTTP/SSE 客户端。"""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        chat_path: Optional[str] = None,
        stream_path: Optional[str] = None,
        request_timeout_seconds: Optional[float] = None,
        connect_timeout_seconds: Optional[float] = None,
        stream_timeout_seconds: Optional[float] = None,
    ):
        self.base_url = _normalize_base_url(base_url or settings.VINCI_BASE_URL)
        self.api_key = str(api_key if api_key is not None else settings.VINCI_API_KEY or "").strip()
        self.chat_path = str(chat_path if chat_path is not None else settings.VINCI_CHAT_PATH or "/api/v1/chat").strip()
        self.stream_path = str(
            stream_path if stream_path is not None else settings.VINCI_STREAM_PATH or "/api/v1/chat/stream"
        ).strip()

        self.request_timeout_seconds = float(
            request_timeout_seconds
            if request_timeout_seconds is not None
            else (settings.VINCI_REQUEST_TIMEOUT_SECONDS or 30.0)
        )
        self.connect_timeout_seconds = float(
            connect_timeout_seconds
            if connect_timeout_seconds is not None
            else (settings.VINCI_CONNECT_TIMEOUT_SECONDS or 8.0)
        )
        self.stream_timeout_seconds = float(
            stream_timeout_seconds
            if stream_timeout_seconds is not None
            else (settings.VINCI_STREAM_TIMEOUT_SECONDS or 120.0)
        )

    def _headers(self, *, trace_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Trace-Id": str(trace_id or "").strip()[:128],
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _normalize_history(
        history: Optional[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in list(history or []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if not role:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    def request_chat(
        self,
        *,
        prompt: str,
        history: list[dict[str, Any]],
        session_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """调用 Vinci 非流式对话接口。"""
        payload = {
            "prompt": str(prompt or ""),
            "history": self._normalize_history(history),
            "session_id": str(session_id or ""),
        }
        timeout = httpx.Timeout(self.request_timeout_seconds, connect=self.connect_timeout_seconds)
        url = _build_url(self.base_url, self.chat_path)
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload, headers=self._headers(trace_id=trace_id))
        except httpx.TimeoutException as exc:
            raise VinciTimeoutError(f"vinci request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise VinciUnavailableError(f"vinci request unavailable: {exc}") from exc

        if response.status_code < 200 or response.status_code >= 300:
            message, error_code = _extract_error_message(response)
            raise VinciHTTPError(
                message,
                status_code=response.status_code,
                error_code=error_code,
                response_body=str(response.text or "")[:2000],
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise VinciHTTPError(
                "vinci returned invalid json",
                status_code=502,
                error_code="invalid_json",
            ) from exc
        return result if isinstance(result, dict) else {"data": result}

    @staticmethod
    def _iter_sse_payload_lines(lines: Iterable[str]) -> Generator[str, None, None]:
        event_name = ""
        data_lines: list[str] = []
        for raw_line in lines:
            line = str(raw_line or "").strip()
            if not line:
                if data_lines:
                    payload = "\n".join(data_lines).strip()
                    if event_name and payload and payload != "[DONE]":
                        yield json.dumps({"type": event_name, "data": payload}, ensure_ascii=False)
                    else:
                        yield payload
                    event_name = ""
                    data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            payload = "\n".join(data_lines).strip()
            if event_name and payload and payload != "[DONE]":
                yield json.dumps({"type": event_name, "data": payload}, ensure_ascii=False)
            else:
                yield payload

    def stream_chat(
        self,
        *,
        prompt: str,
        history: list[dict[str, Any]],
        session_id: str,
        trace_id: str,
    ) -> Generator[dict[str, Any], None, None]:
        """调用 Vinci SSE 对话接口并输出原始事件字典。"""
        payload = {
            "prompt": str(prompt or ""),
            "history": self._normalize_history(history),
            "session_id": str(session_id or ""),
        }
        timeout = httpx.Timeout(self.stream_timeout_seconds, connect=self.connect_timeout_seconds)
        url = _build_url(self.base_url, self.stream_path)

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", url, json=payload, headers=self._headers(trace_id=trace_id)) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        message, error_code = _extract_error_message(response)
                        raise VinciHTTPError(
                            message,
                            status_code=response.status_code,
                            error_code=error_code,
                            response_body=str(response.text or "")[:2000],
                        )

                    for payload_line in self._iter_sse_payload_lines(response.iter_lines()):
                        raw = str(payload_line or "").strip()
                        if not raw:
                            continue
                        if raw == "[DONE]":
                            yield {"type": "done"}
                            return
                        try:
                            event_payload = json.loads(raw)
                        except ValueError:
                            yield {"type": "message.delta", "delta": raw}
                            continue
                        if isinstance(event_payload, dict):
                            yield event_payload
                        else:
                            yield {"type": "message.delta", "delta": str(event_payload)}
        except VinciHTTPError:
            raise
        except httpx.TimeoutException as exc:
            raise VinciTimeoutError(f"vinci stream timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise VinciUnavailableError(f"vinci stream unavailable: {exc}") from exc
