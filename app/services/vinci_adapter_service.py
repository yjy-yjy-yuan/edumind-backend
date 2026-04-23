"""Vinci 适配层：契约标准化、错误映射、降级与遥测。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Any, Callable, Generator, Optional

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


@dataclass
class _CircuitBreakerState:
    failure_count: int = 0
    open_until: float = 0.0
    opened_at: float = 0.0
    last_error_code: str = ""


class VinciAdapterService:
    """Vinci 适配层服务。"""

    _CIRCUITS: dict[str, _CircuitBreakerState] = {}
    _CIRCUIT_LOCK = threading.Lock()

    def __init__(
        self,
        *,
        client: Optional[Any] = None,
        circuit_failure_threshold: Optional[int] = None,
        circuit_recovery_seconds: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.client = client if client is not None else VinciClient()
        self._circuit_failure_threshold = max(
            1,
            int(
                circuit_failure_threshold
                if circuit_failure_threshold is not None
                else getattr(settings, "VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3)
            ),
        )
        self._circuit_recovery_seconds = max(
            1.0,
            float(
                circuit_recovery_seconds
                if circuit_recovery_seconds is not None
                else getattr(settings, "VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS", 30.0)
            ),
        )
        self._clock = clock or monotonic
        client_base_url = str(getattr(self.client, "base_url", "") or "").strip()
        if client_base_url:
            self._circuit_key = client_base_url
        else:
            # 测试注入 client 往往不含 base_url，用对象 id 避免跨用例状态串扰。
            self._circuit_key = f"inmemory-client:{id(self.client)}"

    @classmethod
    def _get_or_create_circuit(cls, key: str) -> _CircuitBreakerState:
        state = cls._CIRCUITS.get(key)
        if state is None:
            state = _CircuitBreakerState()
            cls._CIRCUITS[key] = state
        return state

    def _before_request_circuit_check(self) -> tuple[bool, bool, float]:
        """
        返回 (blocked, probe_mode, opened_at)：
        - blocked=True: 熔断窗口内，直接降级返回
        - probe_mode=True: 恢复窗口到期后允许一次探测请求
        """
        now = float(self._clock())
        with self._CIRCUIT_LOCK:
            state = self._get_or_create_circuit(self._circuit_key)
            if state.open_until > now:
                return True, False, float(state.opened_at)
            if state.open_until > 0.0 and now >= state.open_until:
                # 半开探测：清空 open_until，允许一次调用尝试恢复
                state.open_until = 0.0
                return False, True, float(state.opened_at)
            return False, False, float(state.opened_at)

    def _record_failure(self, *, error_code: str, probe_mode: bool) -> tuple[bool, float]:
        """记录失败；返回 (opened, opened_at)。"""
        now = float(self._clock())
        with self._CIRCUIT_LOCK:
            state = self._get_or_create_circuit(self._circuit_key)
            state.failure_count += 1
            state.last_error_code = str(error_code or "").strip()
            should_open = probe_mode or state.failure_count >= self._circuit_failure_threshold
            if not should_open:
                return False, float(state.opened_at)

            state.failure_count = 0
            state.opened_at = now
            state.open_until = now + self._circuit_recovery_seconds
            return True, float(state.opened_at)

    def _record_success(self) -> bool:
        """成功后重置熔断状态；返回是否发生恢复（曾经打开过熔断）。"""
        with self._CIRCUIT_LOCK:
            state = self._get_or_create_circuit(self._circuit_key)
            recovered = bool(state.opened_at > 0.0 or state.open_until > 0.0)
            state.failure_count = 0
            state.open_until = 0.0
            state.opened_at = 0.0
            state.last_error_code = ""
            return recovered

    def _build_degraded_payload(
        self,
        *,
        safe_history: list[dict[str, Any]],
        safe_session_id: str,
        safe_trace_id: str,
        error_code: str,
        reason: str,
        latency_ms: Optional[float] = None,
        opened_at: Optional[float] = None,
    ) -> dict[str, Any]:
        metadata = {
            "session_id": safe_session_id,
            "reason": str(reason or "").strip() or "degraded",
            "error_code": str(error_code or "").strip() or "VINCI_DEGRADED",
        }
        if opened_at is not None and opened_at > 0:
            metadata["opened_at"] = opened_at
            metadata["recovery_seconds"] = self._circuit_recovery_seconds
        self._emit_event(
            event_type="vinci_request_degraded",
            trace_id=safe_trace_id,
            status=AnalyticsStatus.DEGRADED.value,
            latency_ms=latency_ms,
            metadata=metadata,
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
            "error_code": str(error_code or "VINCI_DEGRADED"),
        }

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
        blocked, probe_mode, opened_at = self._before_request_circuit_check()
        if blocked:
            return self._build_degraded_payload(
                safe_history=safe_history,
                safe_session_id=safe_session_id,
                safe_trace_id=safe_trace_id,
                error_code="VINCI_CIRCUIT_OPEN",
                reason="circuit_open",
                latency_ms=0.0,
                opened_at=opened_at,
            )

        self._emit_event(
            event_type="vinci_request_started",
            trace_id=safe_trace_id,
            status=AnalyticsStatus.STARTED.value,
            metadata={
                "session_id": safe_session_id,
                "circuit_probe": bool(probe_mode),
            },
        )
        logger.info(
            "vinci request started | trace_id=%s | session_id=%s | history_count=%s | probe=%s",
            safe_trace_id,
            safe_session_id,
            len(safe_history),
            probe_mode,
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
            opened, opened_at = self._record_failure(error_code="VINCI_TIMEOUT", probe_mode=probe_mode)
            self._emit_event(
                event_type="vinci_request_timeout",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.TIMEOUT.value,
                latency_ms=latency_ms,
                metadata={"session_id": safe_session_id, "circuit_opened": opened},
            )
            if opened:
                self._emit_event(
                    event_type="vinci_circuit_opened",
                    trace_id=safe_trace_id,
                    status=AnalyticsStatus.DEGRADED.value,
                    metadata={
                        "session_id": safe_session_id,
                        "reason": "timeout",
                        "error_code": "VINCI_TIMEOUT",
                        "opened_at": opened_at,
                        "recovery_seconds": self._circuit_recovery_seconds,
                    },
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
            error_code = f"VINCI_UPSTREAM_{upstream_status}"
            opened, opened_at = self._record_failure(error_code=error_code, probe_mode=probe_mode)
            self._emit_event(
                event_type="vinci_request_error",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.ERROR.value,
                latency_ms=latency_ms,
                metadata={
                    "session_id": safe_session_id,
                    "upstream_status_code": upstream_status,
                    "circuit_opened": opened,
                },
            )
            if opened:
                self._emit_event(
                    event_type="vinci_circuit_opened",
                    trace_id=safe_trace_id,
                    status=AnalyticsStatus.DEGRADED.value,
                    metadata={
                        "session_id": safe_session_id,
                        "reason": "upstream_error",
                        "error_code": error_code,
                        "opened_at": opened_at,
                        "recovery_seconds": self._circuit_recovery_seconds,
                    },
                )
            raise VinciAdapterError(
                str(exc) or "vinci upstream error",
                error_code=error_code,
                trace_id=safe_trace_id,
                upstream_status_code=upstream_status,
                status_code=502,
            ) from exc
        except VinciUnavailableError as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            opened, opened_at = self._record_failure(error_code="VINCI_UNAVAILABLE", probe_mode=probe_mode)
            if opened:
                self._emit_event(
                    event_type="vinci_circuit_opened",
                    trace_id=safe_trace_id,
                    status=AnalyticsStatus.DEGRADED.value,
                    metadata={
                        "session_id": safe_session_id,
                        "reason": "vinci_unavailable",
                        "error_code": "VINCI_UNAVAILABLE",
                        "opened_at": opened_at,
                        "recovery_seconds": self._circuit_recovery_seconds,
                    },
                )
            logger.warning(
                "vinci request degraded | trace_id=%s | session_id=%s | error=%s",
                safe_trace_id,
                safe_session_id,
                exc,
            )
            return self._build_degraded_payload(
                safe_history=safe_history,
                safe_session_id=safe_session_id,
                safe_trace_id=safe_trace_id,
                error_code="VINCI_UNAVAILABLE",
                reason="vinci_unavailable",
                latency_ms=latency_ms,
                opened_at=opened_at if opened else None,
            )

        normalized = self._normalize_response(
            raw_response,
            session_id=safe_session_id,
            trace_id=safe_trace_id,
            fallback_history=safe_history,
        )
        recovered = self._record_success()
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
        if recovered:
            self._emit_event(
                event_type="vinci_circuit_recovered",
                trace_id=safe_trace_id,
                status=AnalyticsStatus.OK.value,
                metadata={"session_id": safe_session_id},
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
