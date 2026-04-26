"""Vinci 微服务客户端（HTTP / SSE）。"""

from __future__ import annotations

import json
from typing import Any, Generator, Iterable, Optional
from urllib.parse import urlparse, urlunparse

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
    def _is_internvl_path(path: str) -> bool:
        return "inference/internvl" in str(path or "").strip().lower()

    @staticmethod
    def _normalize_history_for_internvl(
        history: list[dict[str, Any]],
        max_items: int = 50,
    ) -> list[list[Any]]:
        """将 EduMind 历史格式 {role, content} 转换为 Vinci internvl 格式 [role_code, text]。

        Vinci internvl 期望: history[i] = [role_code (int), text (str)]
        EduMind 内部格式: history[i] = {role: str, content: str}
        """
        result: list[list[Any]] = []
        for item in list(history or [])[-max_items:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if not role or not content:
                continue
            # user=0, assistant=1（与 Vinci internvl 约定一致）
            role_code = 0 if role in ("user", "human") else 1
            result.append([role_code, content])
        return result

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

    def _build_chat_payload(
        self,
        *,
        prompt: str,
        history: list[dict[str, Any]],
        session_id: str,
        path: str,
    ) -> dict[str, Any]:
        normalized_history = self._normalize_history(history)
        safe_prompt = str(prompt or "")
        safe_session_id = str(session_id or "")
        if self._is_internvl_path(path):
            return {
                "question": safe_prompt,
                "history": normalized_history,
                "session_id": safe_session_id,
                "timestamp": 0,
                "silent": False,
                "frames": [],
                "base64_frames": [],
            }
        return {
            "prompt": safe_prompt,
            "history": normalized_history,
            "session_id": safe_session_id,
        }

    def _build_vision_payload(
        self,
        *,
        prompt: str,
        base64_frames: list[str],
        history: list[dict[str, Any]],
        session_id: str,
        silent: bool = False,
        path: str,
    ) -> dict[str, Any]:
        """为 Vinci internvl 构建带图像的推理请求。"""
        safe_prompt = str(prompt or "")
        safe_session_id = str(session_id or "")
        internvl_history = self._normalize_history_for_internvl(history)
        # Vinci internvl 只接受 base64_frames（不接受 URL 列表）
        safe_frames: list[str] = []
        for f in list(base64_frames or []):
            text = str(f or "").strip()
            if text:
                if "," in text:
                    text = text.split(",", 1)[1]
                safe_frames.append(text)

        payload: dict[str, Any] = {
            "question": safe_prompt,
            "history": internvl_history,
            "session_id": safe_session_id,
            "timestamp": 0,
            "silent": bool(silent),
            "frames": [],
            "base64_frames": safe_frames,
        }
        return payload

    def _candidate_chat_targets(self) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = [(self.base_url, self.chat_path)]

        # 官方 Vinci 推理服务接口：/api/v1/inference/internvl（默认端口 18081）
        if not self._is_internvl_path(self.chat_path):
            candidates.append((self.base_url, "/api/v1/inference/internvl"))

            parsed = urlparse(self.base_url)
            host = str(parsed.hostname or "").strip().lower()
            port = parsed.port
            is_local = host in {"127.0.0.1", "localhost"}
            if is_local and port in {None, 8010}:
                scheme = parsed.scheme or "http"
                alt_netloc = f"{host}:18081"
                alt_base_url = urlunparse((scheme, alt_netloc, "", "", "", "")).rstrip("/")
                candidates.append((alt_base_url, "/api/v1/inference/internvl"))

        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for base_url, path in candidates:
            key = (_normalize_base_url(base_url), f"/{str(path or '').lstrip('/')}")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    def request_chat(
        self,
        *,
        prompt: str,
        history: list[dict[str, Any]],
        session_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """调用 Vinci 非流式对话接口。"""
        timeout = httpx.Timeout(self.request_timeout_seconds, connect=self.connect_timeout_seconds)
        request_errors: list[str] = []
        timeout_errors = 0
        chat_targets = self._candidate_chat_targets()
        for index, (base_url, path) in enumerate(chat_targets):
            payload = self._build_chat_payload(
                prompt=prompt,
                history=history,
                session_id=session_id,
                path=path,
            )
            url = _build_url(base_url, path)
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, json=payload, headers=self._headers(trace_id=trace_id))
            except httpx.TimeoutException as exc:
                timeout_errors += 1
                request_errors.append(f"{url} timeout: {exc}")
                continue
            except httpx.RequestError as exc:
                request_errors.append(f"{url} unavailable: {exc}")
                continue

            if response.status_code < 200 or response.status_code >= 300:
                # 404 常见于路径不兼容，尝试下一个候选路径
                if response.status_code == 404 and index < len(chat_targets) - 1:
                    continue
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

        if timeout_errors > 0 and timeout_errors == len(chat_targets):
            raise VinciTimeoutError("vinci request timed out on all candidate endpoints")
        error_hint = "; ".join(request_errors[:3]) if request_errors else "unknown request failure"
        raise VinciUnavailableError(f"vinci request unavailable: {error_hint}")

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

    def request_vision_chat(
        self,
        *,
        prompt: str,
        base64_frames: list[str],
        history: list[dict[str, Any]],
        session_id: str,
        trace_id: str,
        silent: bool = False,
    ) -> dict[str, Any]:
        """调用 Vinci internvl 推理接口（含图像帧）。

        使用 Vinci 官方 /api/v1/inference/internvl 端点。
        自动将 EduMind history 格式转换为 internvl 的 [role_code, text] 格式。
        """
        path = "/api/v1/inference/internvl"
        payload = self._build_vision_payload(
            prompt=prompt,
            base64_frames=base64_frames,
            history=history,
            session_id=session_id,
            silent=silent,
            path=path,
        )
        timeout = httpx.Timeout(self.request_timeout_seconds, connect=self.connect_timeout_seconds)
        url = _build_url(self.base_url, path)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload, headers=self._headers(trace_id=trace_id))
        except httpx.TimeoutException as exc:
            raise VinciTimeoutError(f"vinci vision request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise VinciUnavailableError(f"vinci vision request unavailable: {exc}") from exc

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
                "vinci vision returned invalid json",
                status_code=502,
                error_code="invalid_json",
            ) from exc
        return result if isinstance(result, dict) else {"data": result}
