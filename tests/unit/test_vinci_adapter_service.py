"""Vinci 适配层单测（M1-1：先红后绿）。"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.agents.exceptions import GovernanceError
from app.agents.governance.context import governance_execution_context


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


def _build_adapter(client: Any, scenario: str):
    module = _load_module("app.services.vinci_adapter_service", scenario)
    adapter_cls = _load_attr(module, "VinciAdapterService", scenario)
    try:
        return adapter_cls(client=client)
    except TypeError:
        pytest.fail(
            f"{scenario}: `VinciAdapterService` 需支持 `client=` 注入，便于治理与测试隔离。",
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
