"""Vinci 适配层：契约标准化、错误映射、降级与遥测。"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Generator, Optional

from app.agents.governance.context import ensure_in_governance_context
from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent, AnalyticsStatus
from app.core.config import settings
from app.services.vinci_client import (
    VinciClient,
    VinciHTTPError,
    VinciTimeoutError,
    VinciUnavailableError,
)

logger = logging.getLogger(__name__)


class VinciAdapterError(RuntimeError):
    """Vinci 适配层统一异常。"""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        trace_id: str,
        upstream_status_code: Optional[int] = None,
        status_code: int = 502,
    ):
        super().__init__(message)
        self.error_code = str(error_code or "").strip()
        self.trace_id = str(trace_id or "").strip()[:128]
        self.upstream_status_code = upstream_status_code
        self.status_code = int(status_code or 502)


def _safe_trace_id(trace_id: str) -> str:
    normalized = str(trace_id or "").strip()[:128]
    return normalized or settings.ANALYTICS_TRACE_ID_PLACEHOLDER


def _safe_history(history: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(history or []):
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role:
                normalized.append({"role": role, "content": content})
    return normalized


class VinciAdapterService:
    """Vinci 适配层服务。"""

    def __init__(self, *, client: Optional[Any] = None):
        self.client = client if client is not None else VinciClient()

    @staticmethod
    def _emit_event(
        *,
        event_type: str,
        trace_id: str,
        status: str,
        latency_ms: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            get_telemetry().emit(
                AnalyticsEvent(
                    event_type=event_type,
                    trace_id=_safe_trace_id(trace_id),
                    module="vinci",
                    status=status,
                    latency_ms=latency_ms,
                    metadata=dict(metadata or {}),
                )
            )
        except Exception:
            logger.debug("vinci telemetry emit skipped", exc_info=True)

    @staticmethod
    def _normalize_response(
        raw: Any,
        *,
        session_id: str,
        trace_id: str,
        fallback_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(raw or {}) if isinstance(raw, dict) else {}
        answer = str(payload.get("answer") or payload.get("content") or "").strip()
        normalized_history = payload.get("history")
        if not isinstance(normalized_history, list):
            normalized_history = list(fallback_history)
            if answer:
                normalized_history.append({"role": "assistant", "content": answer})
        return {
            "answer": answer,
            "history": _safe_history(normalized_history),
            "session_id": str(payload.get("session_id") or session_id or "").strip(),
            "trace_id": str(payload.get("trace_id") or trace_id or "").strip()[:128],
            "degraded": bool(payload.get("degraded") or False),
        }

    @staticmethod
    def _normalize_stream_event(raw_event: Any, *, session_id: str, trace_id: str) -> Optional[dict[str, Any]]:
        if isinstance(raw_event, dict):
            event_type = str(raw_event.get("event") or raw_event.get("type") or "").strip().lower()
            if event_type in {"delta", "message.delta", "token", "content.delta"}:
                delta = str(raw_event.get("delta") or raw_event.get("content") or raw_event.get("text") or "")
                return {
                    "event": "delta",
                    "delta": delta,
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
            if event_type in {"done", "complete", "completed", "message.stop"}:
                return {"event": "done", "session_id": session_id, "trace_id": trace_id}
            if event_type == "error":
                return {
                    "event": "error",
                    "error_code": str(raw_event.get("error_code") or "VINCI_STREAM_ERROR"),
                    "message": str(raw_event.get("message") or "vinci stream error"),
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
            if "delta" in raw_event:
                return {
                    "event": "delta",
                    "delta": str(raw_event.get("delta") or ""),
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
            return None
        raw_text = str(raw_event or "").strip()
        if not raw_text:
            return None
        return {
            "event": "delta",
            "delta": raw_text,
            "session_id": session_id,
            "trace_id": trace_id,
        }

    def request_chat(
        self,
        *,
        prompt: str,
        history: list[dict[str, Any]],
        session_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        ensure_in_governance_context()
        safe_trace_id = _safe_trace_id(trace_id)
        safe_session_id = str(session_id or "").strip()
        safe_history = _safe_history(history)
        started = perf_counter()

        self._emit_event(
            event_type="vinci_request_started",
            trace_id=safe_trace_id,
            status=AnalyticsStatus.STARTED.value,
            metadata={"session_id": safe_session_id},
        )
        logger.info(
            "vinci request started | trace_id=%s | session_id=%s | history_count=%s",
            safe_trace_id,
            safe_session_id,
            len(safe_history),
        )

        try:
            raw_response = self.client.request_chat(
                prompt=str(prompt or ""),
                history=safe_history,
                session_id=safe_session_id,
                trace_id=safe_trace_id,
            )
        except VinciTimeoutError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            self._emit_event(
                event_type="vinci_request_timeout",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.TIMEOUT.value,
                latency_ms=latency_ms,
                metadata={"session_id": safe_session_id},
            )
            raise VinciAdapterError(
                str(exc) or "vinci timeout",
                error_code="VINCI_TIMEOUT",
                trace_id=safe_trace_id,
                status_code=504,
            ) from exc
        except VinciHTTPError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            upstream_status = int(getattr(exc, "status_code", 502) or 502)
            self._emit_event(
                event_type="vinci_request_error",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.ERROR.value,
                latency_ms=latency_ms,
                metadata={
                    "session_id": safe_session_id,
                    "upstream_status_code": upstream_status,
                },
            )
            raise VinciAdapterError(
                str(exc) or "vinci upstream error",
                error_code=f"VINCI_UPSTREAM_{upstream_status}",
                trace_id=safe_trace_id,
                upstream_status_code=upstream_status,
                status_code=502,
            ) from exc
        except VinciUnavailableError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            self._emit_event(
                event_type="vinci_request_degraded",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.DEGRADED.value,
                latency_ms=latency_ms,
                metadata={"session_id": safe_session_id, "reason": "vinci_unavailable"},
            )
            logger.warning(
                "vinci request degraded | trace_id=%s | session_id=%s | error=%s",
                safe_trace_id,
                safe_session_id,
                exc,
            )
            degraded_answer = "Vinci 服务暂不可用，已返回降级结果，请稍后重试。"
            degraded_history = list(safe_history)
            degraded_history.append({"role": "assistant", "content": degraded_answer})
            return {
                "answer": degraded_answer,
                "history": degraded_history,
                "session_id": safe_session_id,
                "trace_id": safe_trace_id,
                "degraded": True,
                "error_code": "VINCI_UNAVAILABLE",
            }

        normalized = self._normalize_response(
            raw_response,
            session_id=safe_session_id,
            trace_id=safe_trace_id,
            fallback_history=safe_history,
        )
        latency_ms = round((perf_counter() - started) * 1000, 3)
        self._emit_event(
            event_type="vinci_request_completed",
            trace_id=safe_trace_id,
            status=AnalyticsStatus.OK.value,
            latency_ms=latency_ms,
            metadata={
                "session_id": normalized["session_id"],
                "degraded": bool(normalized.get("degraded")),
                "history_count": len(normalized.get("history") or []),
            },
        )
        logger.info(
            "vinci request completed | trace_id=%s | session_id=%s | degraded=%s",
            safe_trace_id,
            normalized["session_id"],
            normalized.get("degraded"),
        )
        return normalized

    def stream_chat(
        self,
        *,
        prompt: str,
        history: list[dict[str, Any]],
        session_id: str,
        trace_id: str,
    ) -> Generator[dict[str, Any], None, None]:
        ensure_in_governance_context()
        safe_trace_id = _safe_trace_id(trace_id)
        safe_session_id = str(session_id or "").strip()
        safe_history = _safe_history(history)
        started = perf_counter()
        done_emitted = False

        self._emit_event(
            event_type="vinci_stream_started",
            trace_id=safe_trace_id,
            status=AnalyticsStatus.STARTED.value,
            metadata={"session_id": safe_session_id},
        )
        try:
            for raw_event in self.client.stream_chat(
                prompt=str(prompt or ""),
                history=safe_history,
                session_id=safe_session_id,
                trace_id=safe_trace_id,
            ):
                normalized = self._normalize_stream_event(
                    raw_event,
                    session_id=safe_session_id,
                    trace_id=safe_trace_id,
                )
                if not normalized:
                    continue
                if normalized.get("event") == "done":
                    done_emitted = True
                yield normalized
        except VinciTimeoutError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            self._emit_event(
                event_type="vinci_stream_timeout",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.TIMEOUT.value,
                latency_ms=latency_ms,
                metadata={"session_id": safe_session_id},
            )
            yield {
                "event": "error",
                "error_code": "VINCI_TIMEOUT",
                "message": str(exc) or "vinci stream timeout",
                "session_id": safe_session_id,
                "trace_id": safe_trace_id,
            }
        except VinciHTTPError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            upstream_status = int(getattr(exc, "status_code", 502) or 502)
            self._emit_event(
                event_type="vinci_stream_error",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.ERROR.value,
                latency_ms=latency_ms,
                metadata={
                    "session_id": safe_session_id,
                    "upstream_status_code": upstream_status,
                },
            )
            yield {
                "event": "error",
                "error_code": f"VINCI_UPSTREAM_{upstream_status}",
                "message": str(exc) or "vinci upstream stream error",
                "session_id": safe_session_id,
                "trace_id": safe_trace_id,
            }
        except VinciUnavailableError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            self._emit_event(
                event_type="vinci_stream_degraded",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.DEGRADED.value,
                latency_ms=latency_ms,
                metadata={"session_id": safe_session_id, "reason": "vinci_unavailable"},
            )
            yield {
                "event": "error",
                "error_code": "VINCI_UNAVAILABLE",
                "message": str(exc) or "vinci unavailable",
                "session_id": safe_session_id,
                "trace_id": safe_trace_id,
            }
        finally:
            if not done_emitted:
                yield {
                    "event": "done",
                    "session_id": safe_session_id,
                    "trace_id": safe_trace_id,
                }
            latency_ms = round((perf_counter() - started) * 1000, 3)
            self._emit_event(
                event_type="vinci_stream_closed",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.OK.value,
                latency_ms=latency_ms,
                metadata={"session_id": safe_session_id, "done_emitted": True},
            )
