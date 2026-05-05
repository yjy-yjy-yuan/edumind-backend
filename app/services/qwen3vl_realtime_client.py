"""Qwen3-VL realtime video description client."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Generator, Iterable, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class Qwen3VLClientError(RuntimeError):
    """Qwen3-VL client error."""


class Qwen3VLTimeoutError(Qwen3VLClientError):
    """Qwen3-VL request timeout."""


class Qwen3VLUnavailableError(Qwen3VLClientError):
    """Qwen3-VL service unavailable."""


class Qwen3VLHTTPError(Qwen3VLClientError):
    """Qwen3-VL returned non-2xx status."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = int(status_code or 502)


@dataclass
class Qwen3VLHealthResult:
    reachable: bool
    latency_ms: float
    loaded: bool = False
    model: Optional[str] = None
    device: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


def _normalize_base_url(url: str) -> str:
    normalized = str(url or "").strip()
    return (normalized or "http://127.0.0.1:18082").rstrip("/")


def _build_url(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url)}/{str(path or '').lstrip('/')}"


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.text or "").strip() or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if detail:
            return str(detail)
    return f"HTTP {response.status_code}"


class Qwen3VLRealtimeClient:
    """HTTP/SSE client for qwen3vl_realtime_server.py."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        health_path: Optional[str] = None,
        describe_path: Optional[str] = None,
        stream_path: Optional[str] = None,
        connect_timeout_seconds: Optional[float] = None,
        request_timeout_seconds: Optional[float] = None,
        stream_timeout_seconds: Optional[float] = None,
        max_new_tokens: Optional[int] = None,
    ):
        self.base_url = _normalize_base_url(base_url or settings.QWEN3VL_BASE_URL)
        self.health_path = str(health_path or settings.QWEN3VL_HEALTH_PATH or "/health")
        self.describe_path = str(
            describe_path or settings.QWEN3VL_DESCRIBE_PATH or "/api/v1/video/describe"
        )
        self.stream_path = str(
            stream_path
            or settings.QWEN3VL_STREAM_PATH
            or "/api/v1/video/describe/stream"
        )
        self.connect_timeout_seconds = float(
            connect_timeout_seconds
            if connect_timeout_seconds is not None
            else getattr(settings, "QWEN3VL_CONNECT_TIMEOUT_SECONDS", 2.0)
        )
        self.request_timeout_seconds = float(
            request_timeout_seconds
            if request_timeout_seconds is not None
            else getattr(settings, "QWEN3VL_REQUEST_TIMEOUT_SECONDS", 8.0)
        )
        self.stream_timeout_seconds = float(
            stream_timeout_seconds
            if stream_timeout_seconds is not None
            else getattr(settings, "QWEN3VL_STREAM_TIMEOUT_SECONDS", 30.0)
        )
        self.max_new_tokens = int(
            max_new_tokens
            if max_new_tokens is not None
            else getattr(settings, "QWEN3VL_MAX_NEW_TOKENS", 64)
        )
        if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
            logger.setLevel(logging.DEBUG)

    def _debug(self, message: str, *args: Any) -> None:
        if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
            logger.debug("[frame_desc_debug] " + message, *args)

    def health_check(
        self, *, timeout_seconds: Optional[float] = None
    ) -> Qwen3VLHealthResult:
        started = perf_counter()
        timeout_value = float(timeout_seconds or self.connect_timeout_seconds)
        url = _build_url(self.base_url, self.health_path)
        self._debug("qwen3vl health request | url=%s | timeout=%s", url, timeout_value)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout_value, connect=min(timeout_value, 2.0)),
                trust_env=False,
            ) as client:
                response = client.get(url, headers={"Accept": "application/json"})
            latency_ms = round((perf_counter() - started) * 1000, 3)
            if response.status_code < 200 or response.status_code >= 300:
                return Qwen3VLHealthResult(
                    reachable=False,
                    latency_ms=latency_ms,
                    error=_response_detail(response)[:200],
                    error_code=f"HTTP_{response.status_code}",
                )
            payload = response.json()
            return Qwen3VLHealthResult(
                reachable=True,
                latency_ms=latency_ms,
                loaded=bool(payload.get("loaded")),
                model=str(payload.get("model") or "") or None,
                device=str(payload.get("device") or "") or None,
            )
        except httpx.TimeoutException as exc:
            return Qwen3VLHealthResult(
                reachable=False,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                error=str(exc)[:200] or "timeout",
                error_code="QWEN3VL_TIMEOUT",
            )
        except httpx.RequestError as exc:
            return Qwen3VLHealthResult(
                reachable=False,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                error=str(exc)[:200],
                error_code="QWEN3VL_UNAVAILABLE",
            )
        except Exception as exc:  # noqa: BLE001
            return Qwen3VLHealthResult(
                reachable=False,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                error=str(exc)[:200],
                error_code="QWEN3VL_CHECK_FAILED",
            )

    def _payload(
        self,
        *,
        base64_frames: list[str],
        prompt: str,
        max_new_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        return {
            "base64_frames": [
                str(item or "").strip()
                for item in list(base64_frames or [])
                if str(item or "").strip()
            ],
            "prompt": str(prompt or "").strip() or None,
            "max_new_tokens": int(max_new_tokens or self.max_new_tokens),
        }

    def describe(
        self,
        *,
        base64_frames: list[str],
        prompt: str,
        max_new_tokens: Optional[int] = None,
    ) -> str:
        timeout = httpx.Timeout(
            self.request_timeout_seconds, connect=self.connect_timeout_seconds
        )
        url = _build_url(self.base_url, self.describe_path)
        started = perf_counter()
        self._debug(
            "qwen3vl describe request | url=%s | frames=%d | prompt_chars=%d | max_new_tokens=%s | timeout=%s | connect_timeout=%s",
            url,
            len(base64_frames or []),
            len(str(prompt or "")),
            int(max_new_tokens or self.max_new_tokens),
            self.request_timeout_seconds,
            self.connect_timeout_seconds,
        )
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                response = client.post(
                    url,
                    json=self._payload(
                        base64_frames=base64_frames,
                        prompt=prompt,
                        max_new_tokens=max_new_tokens,
                    ),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            self._debug(
                "qwen3vl describe timeout | url=%s | elapsed_ms=%.2f | error=%s",
                url,
                (perf_counter() - started) * 1000,
                exc,
            )
            raise Qwen3VLTimeoutError(f"qwen3vl request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            self._debug(
                "qwen3vl describe request_error | url=%s | elapsed_ms=%.2f | error=%s",
                url,
                (perf_counter() - started) * 1000,
                exc,
            )
            raise Qwen3VLUnavailableError(
                f"qwen3vl request unavailable: {exc}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            self._debug(
                "qwen3vl describe http_error | url=%s | status=%s | elapsed_ms=%.2f | detail=%s",
                url,
                response.status_code,
                (perf_counter() - started) * 1000,
                _response_detail(response)[:300],
            )
            raise Qwen3VLHTTPError(
                _response_detail(response), status_code=response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            self._debug(
                "qwen3vl describe invalid_json | url=%s | elapsed_ms=%.2f | text=%s",
                url,
                (perf_counter() - started) * 1000,
                str(response.text or "")[:300],
            )
            raise Qwen3VLHTTPError(
                "qwen3vl returned invalid json", status_code=502
            ) from exc
        description = str(payload.get("description") or "").strip()
        self._debug(
            "qwen3vl describe response | url=%s | status=%s | elapsed_ms=%.2f | description_chars=%d | empty=%s",
            url,
            response.status_code,
            (perf_counter() - started) * 1000,
            len(description),
            not bool(description),
        )
        return description

    @staticmethod
    def _iter_sse_payload_lines(
        lines: Iterable[str],
    ) -> Generator[tuple[str, str], None, None]:
        event_name = "message"
        data_lines: list[str] = []
        for raw_line in lines:
            line = str(raw_line or "").strip()
            if not line:
                if data_lines:
                    yield event_name, "\n".join(data_lines)
                    event_name = "message"
                    data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            yield event_name, "\n".join(data_lines)

    def stream_describe(
        self,
        *,
        base64_frames: list[str],
        prompt: str,
        max_new_tokens: Optional[int] = None,
    ) -> Generator[dict[str, Any], None, None]:
        timeout = httpx.Timeout(
            self.stream_timeout_seconds, connect=self.connect_timeout_seconds
        )
        accumulated = ""
        url = _build_url(self.base_url, self.stream_path)
        self._debug(
            "qwen3vl stream request | url=%s | frames=%d", url, len(base64_frames or [])
        )
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                with client.stream(
                    "POST",
                    url,
                    json=self._payload(
                        base64_frames=base64_frames,
                        prompt=prompt,
                        max_new_tokens=max_new_tokens,
                    ),
                    headers={
                        "Accept": "text/event-stream",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        response.read()
                        raise Qwen3VLHTTPError(
                            _response_detail(response), status_code=response.status_code
                        )

                    for event_name, data in self._iter_sse_payload_lines(
                        response.iter_lines()
                    ):
                        if event_name == "end":
                            yield {"event": "done"}
                            return
                        if event_name == "error":
                            yield {
                                "event": "error",
                                "error_code": "QWEN3VL_STREAM_ERROR",
                                "message": data,
                            }
                            return
                        token = data
                        try:
                            payload = json.loads(data)
                            if isinstance(payload, dict):
                                token = str(
                                    payload.get("delta")
                                    or payload.get("description")
                                    or payload.get("text")
                                    or ""
                                )
                        except ValueError:
                            pass
                        accumulated += str(token or "")
                        if accumulated.strip():
                            yield {"event": "delta", "delta": accumulated.strip()}
        except Qwen3VLHTTPError:
            raise
        except httpx.TimeoutException as exc:
            raise Qwen3VLTimeoutError(f"qwen3vl stream timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise Qwen3VLUnavailableError(f"qwen3vl stream unavailable: {exc}") from exc
        yield {"event": "done"}
