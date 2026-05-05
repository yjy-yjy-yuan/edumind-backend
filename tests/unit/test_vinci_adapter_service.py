"""Vinci 适配层单测（M1-1：先红后绿）。"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

import pytest

from app.agents.exceptions import GovernanceError
from app.agents.governance.context import governance_execution_context
from app.analytics.pipeline import get_telemetry, reset_telemetry_for_tests


def _load_module(module_name: str, scenario: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        pytest.fail(
            f"{scenario}: 缺少模块 `{module_name}`，请在 M1-2 补齐最小实现。",
            pytrace=False,
        )


def _load_attr(module: Any, attr_name: str, scenario: str):
    value = getattr(module, attr_name, None)
    if value is None:
        pytest.fail(
            f"{scenario}: `{module.__name__}` 缺少 `{attr_name}`，无法满足 Vinci 契约。",
            pytrace=False,
        )
    return value


def _build_adapter(client: Any, scenario: str, **kwargs):
    module = _load_module("app.services.vinci_adapter_service", scenario)
    adapter_cls = _load_attr(module, "VinciAdapterService", scenario)
    try:
        return adapter_cls(client=client, **kwargs)
    except TypeError:
        pytest.fail(
            f"{scenario}: `VinciAdapterService` 需支持注入参数（如 `client=`、熔断配置），便于治理与测试隔离。",
            pytrace=False,
        )


def _load_adapter_error_cls(scenario: str):
    module = _load_module("app.services.vinci_adapter_service", scenario)
    return _load_attr(module, "VinciAdapterError", scenario)


def _load_client_error_cls(error_name: str, scenario: str):
    module = _load_module("app.services.vinci_client", scenario)
    return _load_attr(module, error_name, scenario)


def _build_http_error(error_cls: type[BaseException], status_code: int) -> BaseException:
    kwargs_candidates = (
        {"status_code": status_code, "message": f"upstream_{status_code}"},
        {"message": f"upstream_{status_code}", "status_code": status_code},
    )
    for kwargs in kwargs_candidates:
        try:
            return error_cls(**kwargs)
        except TypeError:
            continue

    try:
        return error_cls(f"upstream_{status_code}", status_code=status_code)
    except TypeError:
        return error_cls(f"upstream_{status_code}")


@pytest.mark.unit
def test_vinci_request_chat_rejects_direct_invocation_outside_governance_context():
    """场景 0：绕过治理网关直接调用适配层应被阻断。"""
    scenario = "绕过治理网关阻断"

    class SuccessClient:
        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            _ = (prompt, history, session_id, trace_id)
            return {"answer": "ok"}

    adapter = _build_adapter(SuccessClient(), scenario)
    with pytest.raises(GovernanceError, match="governance_bypass_blocked"):
        adapter.request_chat(
            prompt="直接调用",
            history=[],
            session_id="sess-direct-1",
            trace_id="trace-direct-1",
        )


@pytest.mark.unit
def test_vinci_timeout_is_mapped_to_unified_error_code():
    """场景 1：超时处理。"""
    scenario = "超时处理"
    timeout_error_cls = _load_client_error_cls("VinciTimeoutError", scenario)
    adapter_error_cls = _load_adapter_error_cls(scenario)

    class TimeoutClient:
        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            raise timeout_error_cls("vinci timeout")

    adapter = _build_adapter(TimeoutClient(), scenario)

    with governance_execution_context():
        with pytest.raises(adapter_error_cls) as exc_info:
            adapter.request_chat(
                prompt="请总结这段内容",
                history=[],
                session_id="sess-timeout-1",
                trace_id="trace-timeout-1",
            )

    exc = exc_info.value
    assert getattr(exc, "error_code", "") == "VINCI_TIMEOUT"
    assert getattr(exc, "trace_id", "") == "trace-timeout-1"


@pytest.mark.unit
def test_vinci_non_2xx_error_is_mapped_with_upstream_status():
    """场景 2：非 2xx 错误映射。"""
    scenario = "非 2xx 错误映射"
    http_error_cls = _load_client_error_cls("VinciHTTPError", scenario)
    adapter_error_cls = _load_adapter_error_cls(scenario)

    class Non2xxClient:
        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            raise _build_http_error(http_error_cls, 429)

    adapter = _build_adapter(Non2xxClient(), scenario)

    with governance_execution_context():
        with pytest.raises(adapter_error_cls) as exc_info:
            adapter.request_chat(
                prompt="解释牛顿第二定律",
                history=[{"role": "user", "content": "F=ma 是什么意思"}],
                session_id="sess-http-1",
                trace_id="trace-http-1",
            )

    exc = exc_info.value
    assert getattr(exc, "error_code", "") == "VINCI_UPSTREAM_429"
    assert getattr(exc, "upstream_status_code", None) == 429
    assert getattr(exc, "trace_id", "") == "trace-http-1"


@pytest.mark.unit
def test_vinci_response_contract_is_normalized_with_history_and_session_id():
    """场景 3：返回契约标准化（含 history/session_id）。"""
    scenario = "返回契约标准化"

    class SuccessClient:
        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            return {
                "answer": "这是 Vinci 的回答",
                "history": history + [{"role": "assistant", "content": "这是 Vinci 的回答"}],
            }

    adapter = _build_adapter(SuccessClient(), scenario)
    with governance_execution_context():
        normalized = adapter.request_chat(
            prompt="什么是函数单调性",
            history=[{"role": "user", "content": "请解释函数单调性"}],
            session_id="sess-contract-1",
            trace_id="trace-contract-1",
        )

    assert normalized["answer"] == "这是 Vinci 的回答"
    assert normalized["session_id"] == "sess-contract-1"
    assert normalized["trace_id"] == "trace-contract-1"
    assert isinstance(normalized["history"], list)
    assert normalized["history"][-1]["role"] == "assistant"


@pytest.mark.unit
def test_vinci_sse_events_are_normalized_and_emit_done_event():
    """场景 4：SSE 事件标准化与结束事件。"""
    scenario = "SSE 事件标准化"

    class StreamClient:
        def stream_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            yield {"type": "message.delta", "delta": "第一段"}
            yield {"type": "message.delta", "delta": "第二段"}

    adapter = _build_adapter(StreamClient(), scenario)
    with governance_execution_context():
        events = list(
            adapter.stream_chat(
                prompt="请分步讲解导数定义",
                history=[{"role": "user", "content": "导数是什么"}],
                session_id="sess-stream-1",
                trace_id="trace-stream-1",
            )
        )

    assert events, "应至少产生一个流式事件"
    assert events[0]["event"] == "delta"
    assert events[0]["delta"] == "第一段"
    assert events[-1]["event"] == "done"
    assert events[-1]["session_id"] == "sess-stream-1"
    assert events[-1]["trace_id"] == "trace-stream-1"


@pytest.mark.unit
def test_vinci_vision_sse_events_are_normalized_and_emit_done_event():
    """Vinci internvl 视觉流事件应被标准化为 delta/done。"""
    scenario = "视觉 SSE 事件标准化"

    class VisionStreamClient:
        def stream_vision_chat(
            self,
            *,
            prompt: str,
            base64_frames: list[str],
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
            silent: bool = False,
        ):
            _ = prompt, base64_frames, history, trace_id, silent
            yield {"type": "message.delta", "delta": "老师正在讲解"}
            yield {"type": "message.delta", "delta": "老师正在讲解例题"}

    adapter = _build_adapter(VisionStreamClient(), scenario)
    with governance_execution_context():
        events = list(
            adapter.stream_vision_chat(
                prompt="描述当前画面",
                base64_frames=["/9j/4AAQSkZJRg=="],
                history=[],
                session_id="sess-vision-stream",
                trace_id="trace-vision-stream",
            )
        )

    assert [e["event"] for e in events] == ["delta", "delta", "done"]
    assert events[0]["delta"] == "老师正在讲解"
    assert events[1]["delta"] == "老师正在讲解例题"
    assert events[-1]["session_id"] == "sess-vision-stream"


@pytest.mark.unit
def test_vinci_unavailable_returns_degraded_response():
    """场景 5：Vinci 不可用时降级响应。"""
    scenario = "Vinci 不可用降级"
    unavailable_error_cls = _load_client_error_cls("VinciUnavailableError", scenario)

    class UnavailableClient:
        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            raise unavailable_error_cls("vinci unavailable")

    adapter = _build_adapter(UnavailableClient(), scenario)
    with governance_execution_context():
        payload = adapter.request_chat(
            prompt="帮我总结上节课重点",
            history=[{"role": "user", "content": "请总结上节课"}],
            session_id="sess-degrade-1",
            trace_id="trace-degrade-1",
        )

    assert payload["degraded"] is True
    assert payload["error_code"] == "VINCI_UNAVAILABLE"
    assert payload["session_id"] == "sess-degrade-1"
    assert payload["trace_id"] == "trace-degrade-1"
    assert isinstance(payload["history"], list)
    assert str(payload.get("answer") or "").strip()


@pytest.mark.unit
def test_vinci_circuit_breaker_opens_and_short_circuits_until_recovery_window():
    """场景 6：连续失败触发熔断，窗口内快速失败。"""
    scenario = "熔断开启与窗口阻断"
    timeout_error_cls = _load_client_error_cls("VinciTimeoutError", scenario)
    adapter_error_cls = _load_adapter_error_cls(scenario)

    class TimeoutClient:
        def __init__(self):
            self.calls = 0

        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            _ = (prompt, history, session_id, trace_id)
            self.calls += 1
            raise timeout_error_cls("vinci timeout")

    clock = {"now": 1000.0}

    def _clock():
        return float(clock["now"])

    client = TimeoutClient()
    adapter = _build_adapter(
        client,
        scenario,
        circuit_failure_threshold=2,
        circuit_recovery_seconds=30.0,
        clock=_clock,
    )

    with governance_execution_context():
        with pytest.raises(adapter_error_cls) as first_exc:
            adapter.request_chat(
                prompt="p1",
                history=[],
                session_id="sess-cb-1",
                trace_id="trace-cb-1",
            )
        assert first_exc.value.error_code == "VINCI_TIMEOUT"
        with pytest.raises(adapter_error_cls) as second_exc:
            adapter.request_chat(
                prompt="p2",
                history=[],
                session_id="sess-cb-1",
                trace_id="trace-cb-2",
            )
        assert second_exc.value.error_code == "VINCI_TIMEOUT"
        degraded = adapter.request_chat(
            prompt="p3",
            history=[],
            session_id="sess-cb-1",
            trace_id="trace-cb-3",
        )

    assert degraded["degraded"] is True
    assert degraded["error_code"] == "VINCI_CIRCUIT_OPEN"
    assert client.calls == 2

    clock["now"] += 31.0
    with governance_execution_context():
        with pytest.raises(adapter_error_cls) as third_exc:
            adapter.request_chat(
                prompt="p4",
                history=[],
                session_id="sess-cb-1",
                trace_id="trace-cb-4",
            )
        assert third_exc.value.error_code == "VINCI_TIMEOUT"
        degraded_again = adapter.request_chat(
            prompt="p5",
            history=[],
            session_id="sess-cb-1",
            trace_id="trace-cb-5",
        )
    assert degraded_again["error_code"] == "VINCI_CIRCUIT_OPEN"
    assert client.calls == 3


@pytest.mark.unit
def test_vinci_circuit_breaker_recovers_after_window_with_probe_success(caplog):
    """场景 7：熔断恢复窗口到期后探测成功，熔断关闭。"""
    scenario = "熔断恢复"
    timeout_error_cls = _load_client_error_cls("VinciTimeoutError", scenario)
    adapter_error_cls = _load_adapter_error_cls(scenario)

    class RecoverClient:
        def __init__(self):
            self.calls = 0

        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            _ = (prompt, history, session_id, trace_id)
            self.calls += 1
            if self.calls == 1:
                raise timeout_error_cls("first timeout")
            return {"answer": "recovered-ok", "history": history}

    clock = {"now": 2000.0}

    def _clock():
        return float(clock["now"])

    caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
    client = RecoverClient()
    adapter = _build_adapter(
        client,
        scenario,
        circuit_failure_threshold=1,
        circuit_recovery_seconds=10.0,
        clock=_clock,
    )

    with governance_execution_context():
        with pytest.raises(adapter_error_cls) as first_exc:
            adapter.request_chat(
                prompt="first",
                history=[],
                session_id="sess-cb-r",
                trace_id="trace-cb-r1",
            )
        assert first_exc.value.error_code == "VINCI_TIMEOUT"
        degraded = adapter.request_chat(
            prompt="second",
            history=[],
            session_id="sess-cb-r",
            trace_id="trace-cb-r2",
        )
    assert degraded["error_code"] == "VINCI_CIRCUIT_OPEN"
    assert client.calls == 1

    clock["now"] += 11.0
    with governance_execution_context():
        recovered = adapter.request_chat(
            prompt="third",
            history=[],
            session_id="sess-cb-r",
            trace_id="trace-cb-r3",
        )
    assert recovered["degraded"] is False
    assert recovered["answer"] == "recovered-ok"
    assert client.calls == 2

    payloads = []
    for record in caplog.records:
        if record.name != "app.analytics.telemetry":
            continue
        try:
            payloads.append(json.loads(record.message))
        except Exception:
            continue
    opened = [p for p in payloads if p.get("event_type") == "vinci_circuit_opened"]
    recovered_events = [p for p in payloads if p.get("event_type") == "vinci_circuit_recovered"]
    assert opened
    assert recovered_events


@pytest.mark.unit
def test_vinci_metrics_cover_success_error_timeout_and_degraded_counts():
    """场景 8：Vinci 指标覆盖 success/error/timeout/degraded 与 P95。"""
    scenario = "指标埋点覆盖"
    timeout_error_cls = _load_client_error_cls("VinciTimeoutError", scenario)
    http_error_cls = _load_client_error_cls("VinciHTTPError", scenario)
    unavailable_error_cls = _load_client_error_cls("VinciUnavailableError", scenario)
    adapter_error_cls = _load_adapter_error_cls(scenario)

    class MixedClient:
        def __init__(self):
            self.calls = 0

        def request_chat(
            self,
            *,
            prompt: str,
            history: list[dict[str, Any]],
            session_id: str,
            trace_id: str,
        ):
            _ = (prompt, history, session_id, trace_id)
            self.calls += 1
            if self.calls == 1:
                return {"answer": "ok", "history": history}
            if self.calls == 2:
                raise _build_http_error(http_error_cls, 503)
            if self.calls == 3:
                raise timeout_error_cls("timeout")
            raise unavailable_error_cls("unavailable")

    reset_telemetry_for_tests()
    adapter = _build_adapter(MixedClient(), scenario, circuit_failure_threshold=99)

    with governance_execution_context():
        ok_payload = adapter.request_chat(
            prompt="s1",
            history=[],
            session_id="sess-metrics",
            trace_id="trace-metrics-1",
        )
        with pytest.raises(adapter_error_cls) as err_exc:
            adapter.request_chat(
                prompt="s2",
                history=[],
                session_id="sess-metrics",
                trace_id="trace-metrics-2",
            )
        assert err_exc.value.error_code == "VINCI_UPSTREAM_503"
        with pytest.raises(adapter_error_cls) as timeout_exc:
            adapter.request_chat(
                prompt="s3",
                history=[],
                session_id="sess-metrics",
                trace_id="trace-metrics-3",
            )
        assert timeout_exc.value.error_code == "VINCI_TIMEOUT"
        degraded = adapter.request_chat(
            prompt="s4",
            history=[],
            session_id="sess-metrics",
            trace_id="trace-metrics-4",
        )

    assert ok_payload["degraded"] is False
    assert degraded["degraded"] is True
    assert degraded["error_code"] == "VINCI_UNAVAILABLE"

    metrics = get_telemetry().module_metrics("vinci")
    assert metrics["success_count"] >= 1
    assert metrics["error_count"] >= 1
    assert metrics["timeout_count"] >= 1
    assert metrics["degraded_count"] >= 1
    assert metrics["p95_latency_ms"] is not None
