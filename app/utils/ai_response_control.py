"""High-concurrency response control for AI calls.

The controller keeps AI endpoints useful under pressure: bounded upstream
concurrency, short timeouts, circuit breaking, dynamic output budgets, and
local fallback answers when an upstream provider is unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Iterable, Optional, TypeVar

import requests

from app.core.config import settings
from app.services.video.content import clean_multiline_text, clean_whitespace

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LoadLevel(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class UpstreamCircuitOpen(RuntimeError):
    """Raised when the provider circuit is open and the call must be skipped."""


class UpstreamCapacityExceeded(RuntimeError):
    """Raised when local upstream concurrency is already saturated."""


class AIAdmissionRejected(RuntimeError):
    """Raised when an AI request is rejected before entering business logic."""


class UpstreamHTTPStatusError(RuntimeError):
    """Provider returned a failing HTTP status."""

    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = int(status_code)
        super().__init__(f"HTTP {self.status_code}: {detail}")


@dataclass(frozen=True)
class ResponseBudget:
    load_level: LoadLevel
    max_tokens: int
    timeout_seconds: float
    temperature: float
    answer_char_limit: int
    status_message: str

    @property
    def degraded(self) -> bool:
        return self.load_level != LoadLevel.NORMAL


@dataclass
class CircuitState:
    failure_count: int = 0
    opened_until: float = 0.0
    last_reason: str = ""
    recent_failures: deque[float] | None = None


class TTLCache:
    def __init__(self, *, max_items: int, ttl_seconds: float):
        self.max_items = max(1, int(max_items))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at < now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: str, value: str) -> None:
        if not value:
            return
        with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


class AIResponseController:
    def __init__(self):
        max_concurrency = max(1, int(getattr(settings, "AI_UPSTREAM_MAX_CONCURRENCY", 8)))
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._async_semaphore: asyncio.BoundedSemaphore | None = None
        self._async_semaphore_loop: asyncio.AbstractEventLoop | None = None
        self._max_concurrency = max_concurrency
        self._active_calls = 0
        self._active_lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=80)
        self._timeout_count = 0
        self._cancellation_count = 0
        self._circuits: dict[str, CircuitState] = {}
        self._circuit_lock = threading.Lock()
        self._cache = TTLCache(
            max_items=int(getattr(settings, "AI_RESPONSE_CACHE_MAX_ITEMS", 256)),
            ttl_seconds=float(getattr(settings, "AI_RESPONSE_CACHE_TTL_SECONDS", 300)),
        )

    def budget(self) -> ResponseBudget:
        active = self.active_calls
        admission = admission_controller.snapshot()
        avg_latency = self.average_latency_seconds
        load_avg = self._system_load_average()
        loop_lag = event_loop_monitor.lag_seconds
        high_active = max(2, int(self._max_concurrency * 0.7))
        extreme_active = max(2, int(self._max_concurrency))
        queue_pressure = admission["waiting"] >= max(1, int(settings.AI_ADMISSION_MAX_WAITING_REQUESTS * 0.5))
        extreme_queue_pressure = admission["waiting"] >= max(1, int(settings.AI_ADMISSION_MAX_WAITING_REQUESTS * 0.9))

        if (
            active >= extreme_active
            or avg_latency > 8.0
            or load_avg >= 8.0
            or extreme_queue_pressure
            or loop_lag >= float(getattr(settings, "AI_EVENT_LOOP_LAG_EXTREME_SECONDS", 0.75))
        ):
            return ResponseBudget(
                load_level=LoadLevel.EXTREME,
                max_tokens=int(getattr(settings, "AI_EXTREME_MAX_TOKENS", 96)),
                timeout_seconds=float(getattr(settings, "AI_EXTREME_TIMEOUT_SECONDS", 3.0)),
                temperature=0.1,
                answer_char_limit=180,
                status_message="当前负载很高，已返回极简答案。",
            )
        if (
            active >= high_active
            or avg_latency > 5.0
            or load_avg >= 4.0
            or queue_pressure
            or loop_lag >= float(getattr(settings, "AI_EVENT_LOOP_LAG_HIGH_SECONDS", 0.25))
        ):
            return ResponseBudget(
                load_level=LoadLevel.HIGH,
                max_tokens=int(getattr(settings, "AI_HIGH_MAX_TOKENS", 256)),
                timeout_seconds=float(getattr(settings, "AI_HIGH_TIMEOUT_SECONDS", 5.0)),
                temperature=0.15,
                answer_char_limit=500,
                status_message="当前负载较高，已返回简化答案。",
            )
        return ResponseBudget(
            load_level=LoadLevel.NORMAL,
            max_tokens=int(getattr(settings, "AI_NORMAL_MAX_TOKENS", 1024)),
            timeout_seconds=float(getattr(settings, "AI_NORMAL_TIMEOUT_SECONDS", 8.0)),
            temperature=0.2,
            answer_char_limit=1200,
            status_message="",
        )

    @property
    def active_calls(self) -> int:
        with self._active_lock:
            return self._active_calls

    @property
    def average_latency_seconds(self) -> float:
        if not self._latencies:
            return 0.0
        return sum(self._latencies) / len(self._latencies)

    def cache_key(self, *, provider: str, model: str, messages: Iterable[dict]) -> str:
        raw = "|".join(
            f"{item.get('role', '')}:{clean_whitespace(str(item.get('content', '')))[:800]}" for item in messages
        )
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
        return f"{clean_whitespace(provider).lower()}:{clean_whitespace(model).lower()}:{digest}"

    def get_cached(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def set_cached(self, key: str, value: str) -> None:
        self._cache.set(key, value)

    def execute_upstream(self, provider: str, call: Callable[[ResponseBudget], T]) -> T:
        budget = self.budget()
        self._raise_if_circuit_open(provider)

        acquire_timeout = float(getattr(settings, "AI_UPSTREAM_ACQUIRE_TIMEOUT_SECONDS", 0.05))
        acquired = self._semaphore.acquire(timeout=max(0.0, acquire_timeout))
        if not acquired:
            raise UpstreamCapacityExceeded("upstream_concurrency_saturated")

        start = time.perf_counter()
        with self._active_lock:
            self._active_calls += 1
        try:
            result = call(budget)
            self._record_success(provider, time.perf_counter() - start)
            return result
        except Exception as exc:
            self._record_failure(provider, exc)
            raise
        finally:
            with self._active_lock:
                self._active_calls = max(0, self._active_calls - 1)
            self._semaphore.release()

    async def execute_upstream_async(self, provider: str, call: Callable[[ResponseBudget], Awaitable[T]]) -> T:
        budget = self.budget()
        self._raise_if_circuit_open(provider)

        acquire_timeout = float(getattr(settings, "AI_UPSTREAM_ACQUIRE_TIMEOUT_SECONDS", 0.05))
        semaphore = self._get_async_semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=max(0.0, acquire_timeout))
        except asyncio.TimeoutError as exc:
            raise UpstreamCapacityExceeded("upstream_concurrency_saturated") from exc

        start = time.perf_counter()
        with self._active_lock:
            self._active_calls += 1
        try:
            hard_timeout = min(
                float(getattr(settings, "AI_REQUEST_HARD_TIMEOUT_SECONDS", 10.0)),
                max(1.0, float(budget.timeout_seconds) + 1.0),
            )
            result = await asyncio.wait_for(call(budget), timeout=hard_timeout)
            self._record_success(provider, time.perf_counter() - start)
            return result
        except asyncio.CancelledError:
            self._cancellation_count += 1
            raise
        except asyncio.TimeoutError as exc:
            self._timeout_count += 1
            self._record_failure(provider, exc)
            raise
        except Exception as exc:
            self._record_failure(provider, exc)
            raise
        finally:
            with self._active_lock:
                self._active_calls = max(0, self._active_calls - 1)
            semaphore.release()

    def _get_async_semaphore(self) -> asyncio.BoundedSemaphore:
        loop = asyncio.get_running_loop()
        if self._async_semaphore is None or self._async_semaphore_loop is not loop:
            self._async_semaphore = asyncio.BoundedSemaphore(self._max_concurrency)
            self._async_semaphore_loop = loop
        return self._async_semaphore

    def _raise_if_circuit_open(self, provider: str) -> None:
        now = time.monotonic()
        with self._circuit_lock:
            state = self._circuits.get(provider)
            if state and state.opened_until > now:
                raise UpstreamCircuitOpen(state.last_reason or "upstream_circuit_open")

    def _record_success(self, provider: str, latency_seconds: float) -> None:
        self._latencies.append(max(0.0, latency_seconds))
        with self._circuit_lock:
            self._circuits[provider] = CircuitState()

    def _record_failure(self, provider: str, exc: Exception) -> None:
        reason = classify_upstream_failure(exc)
        if not reason:
            return
        threshold = max(1, int(getattr(settings, "AI_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 2)))
        recovery_seconds = max(1.0, float(getattr(settings, "AI_CIRCUIT_BREAKER_RECOVERY_SECONDS", 20.0)))
        now = time.monotonic()
        with self._circuit_lock:
            state = self._circuits.setdefault(provider, CircuitState())
            if state.recent_failures is None:
                state.recent_failures = deque(maxlen=max(3, threshold * 2))
            state.recent_failures.append(now)
            while state.recent_failures and now - state.recent_failures[0] > 30.0:
                state.recent_failures.popleft()
            state.failure_count += 1
            state.last_reason = reason
            if state.failure_count >= threshold or len(state.recent_failures) >= threshold:
                state.opened_until = now + recovery_seconds
                logger.warning(
                    "AI upstream circuit opened | provider=%s | reason=%s | recovery_seconds=%.1f",
                    provider,
                    reason,
                    recovery_seconds,
                )

    def _system_load_average(self) -> float:
        try:
            if hasattr(os, "getloadavg"):
                return float(os.getloadavg()[0])
        except OSError:
            return 0.0
        return 0.0

    @property
    def timeout_count(self) -> int:
        return self._timeout_count

    @property
    def cancellation_count(self) -> int:
        return self._cancellation_count

    def metrics(self) -> dict:
        return {
            "active_upstream_calls": self.active_calls,
            "max_upstream_concurrency": self._max_concurrency,
            "average_upstream_latency_seconds": round(self.average_latency_seconds, 4),
            "timeouts": self.timeout_count,
            "cancellations": self.cancellation_count,
            "budget": self.budget().__dict__,
        }


def classify_upstream_failure(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            code = 0
        if code == 429:
            return "rate_limited"
        if 500 <= code <= 599:
            return "server_error"
        if code >= 400:
            return "http_error"
    if isinstance(exc, (UpstreamCircuitOpen, UpstreamCapacityExceeded)):
        return str(exc) or exc.__class__.__name__
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "network_unreachable"
    if isinstance(exc, requests.RequestException):
        return "request_failed"
    message = str(exc).lower()
    markers = (
        "timeout",
        "timed out",
        "dns",
        "nameresolution",
        "name resolution",
        "429",
        " 5",
        "http 5",
        "network",
        "connection",
        "temporarily unavailable",
        "rate limit",
        "service unavailable",
        "server error",
    )
    return "upstream_failed" if any(marker in message for marker in markers) else ""


class EventLoopMonitor:
    def __init__(self):
        self._lag_seconds = 0.0
        self._task: asyncio.Task | None = None
        self._lock = threading.Lock()

    @property
    def lag_seconds(self) -> float:
        return self._lag_seconds

    def start(self) -> None:
        with self._lock:
            if self._task and not self._task.done():
                return
            self._task = asyncio.create_task(self._run(), name="ai-event-loop-lag-monitor")

    async def stop(self) -> None:
        task = self._task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        interval = 0.25
        loop = asyncio.get_running_loop()
        expected = loop.time() + interval
        while True:
            await asyncio.sleep(interval)
            now = loop.time()
            self._lag_seconds = max(0.0, now - expected)
            expected = now + interval

    def metrics(self) -> dict:
        try:
            task_count = len(asyncio.all_tasks())
        except RuntimeError:
            task_count = 0
        return {"event_loop_lag_seconds": round(self._lag_seconds, 4), "asyncio_task_count": task_count}


class AIAdmissionController:
    def __init__(self):
        self._max_active = max(1, int(getattr(settings, "AI_ADMISSION_MAX_ACTIVE_REQUESTS", 80)))
        self._max_waiting = max(0, int(getattr(settings, "AI_ADMISSION_MAX_WAITING_REQUESTS", 40)))
        self._per_user_active = max(1, int(getattr(settings, "AI_ADMISSION_PER_USER_ACTIVE_REQUESTS", 6)))
        self._semaphore: asyncio.BoundedSemaphore | None = None
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active = 0
        self._waiting = 0
        self._rejected = 0
        self._accepted = 0
        self._timed_out = 0
        self._wait_times: deque[float] = deque(maxlen=200)
        self._active_by_user: dict[str, int] = defaultdict(int)

    async def acquire(self, *, user_key: str, path: str):
        if not bool(getattr(settings, "AI_ADMISSION_ENABLED", True)):
            return _AdmissionToken(self, user_key, False)

        started = time.perf_counter()
        semaphore, lock = self._get_async_primitives()
        async with lock:
            if self._waiting >= self._max_waiting:
                self._rejected += 1
                raise AIAdmissionRejected("ai_queue_full")
            if self._active_by_user[user_key] >= self._per_user_active:
                self._rejected += 1
                raise AIAdmissionRejected("ai_user_throttled")
            self._waiting += 1

        wait_timeout = max(0.0, float(getattr(settings, "AI_ADMISSION_WAIT_TIMEOUT_SECONDS", 0.15)))
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=wait_timeout)
        except asyncio.TimeoutError as exc:
            async with lock:
                self._waiting = max(0, self._waiting - 1)
                self._timed_out += 1
                self._rejected += 1
            raise AIAdmissionRejected("ai_admission_timeout") from exc

        wait_time = time.perf_counter() - started
        async with lock:
            self._waiting = max(0, self._waiting - 1)
            self._active += 1
            self._accepted += 1
            self._active_by_user[user_key] += 1
            self._wait_times.append(wait_time)
        return _AdmissionToken(self, user_key, True)

    async def release(self, user_key: str) -> None:
        semaphore, lock = self._get_async_primitives()
        async with lock:
            self._active = max(0, self._active - 1)
            current = self._active_by_user.get(user_key, 0)
            if current <= 1:
                self._active_by_user.pop(user_key, None)
            else:
                self._active_by_user[user_key] = current - 1
        semaphore.release()

    def _get_async_primitives(self) -> tuple[asyncio.BoundedSemaphore, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._semaphore is None or self._lock is None:
            self._loop = loop
            self._semaphore = asyncio.BoundedSemaphore(self._max_active)
            self._lock = asyncio.Lock()
        return self._semaphore, self._lock

    def snapshot(self) -> dict:
        waits = list(self._wait_times)
        avg_wait = sum(waits) / len(waits) if waits else 0.0
        max_wait = max(waits) if waits else 0.0
        return {
            "active": self._active,
            "waiting": self._waiting,
            "accepted": self._accepted,
            "rejected": self._rejected,
            "timed_out": self._timed_out,
            "max_active": self._max_active,
            "max_waiting": self._max_waiting,
            "avg_wait_seconds": round(avg_wait, 4),
            "max_wait_seconds": round(max_wait, 4),
        }


class _AdmissionToken:
    def __init__(self, owner: AIAdmissionController, user_key: str, acquired: bool):
        self._owner = owner
        self._user_key = user_key
        self._acquired = acquired

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._acquired:
            await self._owner.release(self._user_key)


def compact_answer(answer: str, budget: ResponseBudget) -> str:
    cleaned = clean_multiline_text(answer)
    if not cleaned:
        return ""
    limit = max(80, int(budget.answer_char_limit))
    if len(cleaned) <= limit:
        return cleaned
    suffix = "。如需完整展开，可以继续追问。"
    return clean_multiline_text(cleaned[: max(60, limit - len(suffix))].rstrip("，。；;,. ") + suffix)


def build_local_fallback_answer(
    question: str,
    *,
    mode: str,
    context_text: str = "",
    reason: str = "",
    budget: Optional[ResponseBudget] = None,
) -> str:
    active_budget = budget or controller.budget()
    prefix = "当前模型服务繁忙，已返回简化答案。"
    normalized_question = clean_whitespace(question)
    normalized_context = clean_multiline_text(context_text)
    reason_summary = summarize_degradation_reason(reason)
    reason_text = f"原因：{reason_summary}。" if reason_summary and active_budget.load_level == LoadLevel.NORMAL else ""

    if normalized_context:
        context_preview = normalized_context[:360]
        answer = (
            f"{prefix}{reason_text}\n"
            f"问题：{normalized_question[:120] or '未提供明确问题'}\n"
            f"可用依据：{context_preview}\n"
            "结论：请先参考以上片段；如果需要更完整解释，可稍后重试或继续追问。"
        )
    elif mode == "free":
        answer = (
            f"{prefix}{reason_text}\n"
            f"你的问题是：{normalized_question[:160] or '未提供明确问题'}。\n"
            "建议先按关键词拆解问题，确认目标、约束和期望输出；模型恢复后可继续展开。"
        )
    else:
        answer = f"{prefix}{reason_text}当前没有足够上下文生成完整回答，请稍后重试或补充更明确的问题。"

    return compact_answer(answer, active_budget)


def summarize_degradation_reason(reason: str) -> str:
    text = clean_whitespace(reason).lower()
    if not text:
        return ""
    if "dns" in text or "name resolution" in text or "nameresolution" in text or "failed to resolve" in text:
        return "上游网络解析失败"
    if "429" in text or "rate limit" in text:
        return "上游限流"
    if "timeout" in text or "timed out" in text:
        return "上游响应超时"
    if "circuit" in text:
        return "上游熔断中"
    if "concurrency" in text or "saturated" in text:
        return "本地并发已满"
    if "connection" in text or "network" in text:
        return "上游网络不可达"
    if "5" in text:
        return "上游服务异常"
    return "上游暂不可用"


controller = AIResponseController()
event_loop_monitor = EventLoopMonitor()
admission_controller = AIAdmissionController()
