"""Cloud Qwen-VL client for frame description fallback.

This client uses the DashScope/OpenAI-compatible chat completions API. It is
only intended as a Qwen-family fallback when the local Qwen3-VL realtime service
is unavailable.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from time import perf_counter
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_frame_desc_debug_logger() -> logging.Logger:
    """Lazy import to avoid circular dependency."""
    from app.services.frame_desc.debug import get_frame_description_debug_logger

    return get_frame_description_debug_logger()


class QwenVLCloudClientError(RuntimeError):
    """Cloud Qwen-VL client error."""


class QwenVLCloudConfigError(QwenVLCloudClientError):
    """Cloud Qwen-VL configuration error."""


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return _clean_text(response.text) or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return _clean_text(error.get("message") or error.get("code"))
        detail = payload.get("detail") or payload.get("message")
        if detail:
            return _clean_text(detail)
    return f"HTTP {response.status_code}"


def _extract_answer(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return str(content or "").strip()


class QwenVLCloudClient:
    """OpenAI-compatible client for DashScope Qwen-VL models."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.api_key = _clean_text(api_key or settings.QWEN_API_KEY or settings.OPENAI_API_KEY)
        self.base_url = _clean_text(base_url or settings.QWEN_API_BASE or settings.OPENAI_BASE_URL).rstrip("/")
        self.model = _clean_text(model or getattr(settings, "FRAME_DESC_CLOUD_QWEN_MODEL", "")) or "qwen3-vl-plus"
        self.timeout_seconds = max(
            1.0,
            float(
                timeout_seconds
                if timeout_seconds is not None
                else getattr(settings, "FRAME_DESC_CLOUD_QWEN_TIMEOUT_SECONDS", 45.0)
            ),
        )
        self.max_tokens = max(
            16,
            int(max_tokens if max_tokens is not None else getattr(settings, "FRAME_DESC_CLOUD_QWEN_MAX_TOKENS", 256)),
        )

    def describe(
        self,
        *,
        base64_frames: list[str],
        prompt: str,
        trace_id: str = "",
        session_id: str = "",
    ) -> str:
        if not self.api_key or not self.base_url:
            raise QwenVLCloudConfigError(
                "cloud_qwen_vl_not_configured:missing QWEN_API_KEY/OPENAI_API_KEY or QWEN_API_BASE"
            )

        safe_frames = [str(frame or "").strip() for frame in list(base64_frames or []) if frame]
        content: list[dict[str, Any]] = [{"type": "text", "text": str(prompt or "").strip()}]
        for frame in safe_frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame}"},
                }
            )

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        started = perf_counter()
        frame_desc_debug_logger = _get_frame_desc_debug_logger()
        frame_desc_debug_logger.debug(
            "cloud_qwen_vl call start | trace_id=%s | session_id=%s | model=%s | base_url=%s | base64_frames_count=%d | prompt_length=%d | timeout=%s",
            trace_id,
            session_id,
            self.model,
            self.base_url,
            len(safe_frames),
            len(str(prompt or "")),
            self.timeout_seconds,
        )
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 5.0)),
                trust_env=False,
            ) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise QwenVLCloudClientError(f"cloud_qwen_vl_timeout:{exc}") from exc
        except httpx.RequestError as exc:
            raise QwenVLCloudClientError(f"cloud_qwen_vl_request_error:{exc}") from exc

        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        if response.status_code < 200 or response.status_code >= 300:
            detail = _response_detail(response)[:300]
            frame_desc_debug_logger.debug(
                "cloud_qwen_vl http_error | trace_id=%s | session_id=%s | status=%s | elapsed_ms=%s | error=%s",
                trace_id,
                session_id,
                response.status_code,
                elapsed_ms,
                detail,
            )
            raise QwenVLCloudClientError(f"cloud_qwen_vl_http_{response.status_code}:{detail}")

        try:
            answer = _extract_answer(response.json())
        except ValueError as exc:
            raise QwenVLCloudClientError("cloud_qwen_vl_invalid_json") from exc

        frame_desc_debug_logger.debug(
            "cloud_qwen_vl call done | trace_id=%s | session_id=%s | model=%s | elapsed_ms=%s | answer_len=%d",
            trace_id,
            session_id,
            self.model,
            elapsed_ms,
            len(answer),
        )
        if not answer:
            raise QwenVLCloudClientError("cloud_qwen_vl_empty_answer")
        return answer
