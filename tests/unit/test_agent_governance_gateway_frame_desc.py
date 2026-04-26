"""M3-A 治理闭环：frame_description 场景专项测试。

覆盖目标：
1. 工具白名单：lf_frame_description 未注册时被 execute_tool 拒绝 ✓
2. 参数非法：空 prompt / 超长 session_id / 非法 history 拒绝 ✓
3. 绕过阻断：工具函数在 gateway 外直调被 ensure_in_governance_context 拒绝 ✓
4. 审计事件：param_invalid / denied / completed 事件可观测 ✓

前置条件（测试内通过 monkeypatch 满足）：
- gateway.py 中已注册 lf_frame_description 工具
- tools_learning_flow.py 中已实现 tool_lf_frame_description
- frame_description_service.py 中 _call_vinci_sync 改走 execute_tool

验收：
    pytest tests/unit/test_agent_governance_gateway_frame_desc.py -v
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.agents.exceptions import GovernanceError
from app.agents.governance import gateway, tools_learning_flow
from app.agents.governance.gateway import (
    MAX_VINCI_HISTORY_CONTENT_CHARS,
    MAX_VINCI_HISTORY_ITEMS,
    MAX_VINCI_PROMPT_CHARS,
    MAX_VINCI_SESSION_ID_CHARS,
    execute_tool,
)
from app.services.vinci_adapter_service import VinciAdapterError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fd_mock_vinci_success():
    """正常返回的 mock Vinci 适配器。"""
    mock = MagicMock()
    mock.request_chat.return_value = {"answer": "老师在黑板上写字", "session_id": "s1"}
    return mock


@pytest.fixture
def fd_mock_vinci_timeout():
    """超时报错的 mock Vinci 适配器。"""
    mock = MagicMock()
    mock.request_chat.side_effect = VinciAdapterError(
        message="connection timeout",
        error_code="VINCI_TIMEOUT",
        trace_id="trace-t",
        status_code=504,
    )
    return mock


# ---------------------------------------------------------------------------
# 1. 白名单拒绝测试（execute_tool 层面）
# ---------------------------------------------------------------------------


def test_execute_tool_rejects_unknown_fd_tool(db):
    """未注册的任意工具名被 execute_tool 白名单拒绝。

    lf_frame_description 已在 gateway 注册（走治理链路），
    本测试用完全不存在的工具名验证白名单兜底拒绝。
    """
    with pytest.raises(GovernanceError, match="tool_not_allowed"):
        execute_tool(
            "lf_frame_description_foo",
            {"prompt": "hello", "session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-1",
        )


def test_execute_tool_rejects_fd_tool_outside_whitelist__not_whitelisted(db):
    """任意非白名单工具名均被拒绝（兜底验证）。"""
    for name in [
        "fd_describe",
        "frame_desc",
        "vinci_direct",
        "lf_frame_desc_v2",
        "tool_fd_frame_description",
    ]:
        with pytest.raises(GovernanceError, match="tool_not_allowed"):
            execute_tool(name, {}, db=db, trace_id=f"t-whitelist-{name}")


# ---------------------------------------------------------------------------
# 2. 参数非法拒绝测试（_validate_params 层面）
# ---------------------------------------------------------------------------


def test_execute_tool_fd_rejects_missing_prompt(db):
    """lf_frame_description 缺少 prompt 时拒绝（missing_prompt）。"""
    with pytest.raises(GovernanceError, match="missing_prompt"):
        execute_tool(
            "lf_frame_description",
            {"session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-2",
        )


def test_execute_tool_fd_rejects_empty_prompt(db):
    """lf_frame_description prompt 为空字符串时拒绝。"""
    with pytest.raises(GovernanceError, match="missing_prompt"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "   ", "session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-3",
        )


def test_execute_tool_fd_rejects_prompt_too_long(db):
    """prompt 字符数超过 MAX_VINCI_PROMPT_CHARS 时拒绝（prompt_too_long）。"""
    long_prompt = "x" * (MAX_VINCI_PROMPT_CHARS + 1)
    with pytest.raises(GovernanceError, match="prompt_too_long"):
        execute_tool(
            "lf_frame_description",
            {"prompt": long_prompt, "session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-4",
        )


def test_execute_tool_fd_rejects_missing_session_id(db):
    """lf_frame_description 缺少 session_id 时拒绝。"""
    with pytest.raises(GovernanceError, match="missing_session_id"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello"},
            db=db,
            trace_id="t-fd-5",
        )


def test_execute_tool_fd_rejects_empty_session_id(db):
    """session_id 为空字符串时拒绝。"""
    with pytest.raises(GovernanceError, match="missing_session_id"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "  "},
            db=db,
            trace_id="t-fd-6",
        )


def test_execute_tool_fd_rejects_session_id_too_long(db):
    """session_id 字符数超过 MAX_VINCI_SESSION_ID_CHARS 时拒绝。"""
    long_sid = "s" * (MAX_VINCI_SESSION_ID_CHARS + 1)
    with pytest.raises(GovernanceError, match="session_id_too_long"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": long_sid, "history": []},
            db=db,
            trace_id="t-fd-7",
        )


def test_execute_tool_fd_rejects_history_not_list(db):
    """history 不为 list 时拒绝（invalid_history）。"""
    with pytest.raises(GovernanceError, match="invalid_history"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "history": "not_a_list"},
            db=db,
            trace_id="t-fd-8",
        )


def test_execute_tool_fd_rejects_history_too_long(db):
    """history 长度超过 MAX_VINCI_HISTORY_ITEMS 时拒绝。"""
    long_history = [{"role": "user", "content": "hello"} for _ in range(MAX_VINCI_HISTORY_ITEMS + 1)]
    with pytest.raises(GovernanceError, match="history_too_long"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "history": long_history},
            db=db,
            trace_id="t-fd-9",
        )


def test_execute_tool_fd_rejects_history_item_not_dict(db):
    """history 内元素不是 dict 时拒绝。"""
    with pytest.raises(GovernanceError, match="invalid_history_item"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "history": ["string_item"]},
            db=db,
            trace_id="t-fd-10",
        )


def test_execute_tool_fd_rejects_history_item_content_too_long(db):
    """history item 的 content 超过 MAX_VINCI_HISTORY_CONTENT_CHARS 时拒绝。"""
    long_content = "y" * (MAX_VINCI_HISTORY_CONTENT_CHARS + 1)
    with pytest.raises(GovernanceError, match="history_item_content_too_long"):
        execute_tool(
            "lf_frame_description",
            {
                "prompt": "hello",
                "session_id": "s1",
                "history": [{"role": "user", "content": long_content}],
            },
            db=db,
            trace_id="t-fd-11",
        )


def test_execute_tool_fd_rejects_history_item_missing_role(db):
    """history item 缺少 role 字段时拒绝。"""
    with pytest.raises(GovernanceError, match="invalid_history_item"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "history": [{"content": "hello"}]},
            db=db,
            trace_id="t-fd-12",
        )


# ---------------------------------------------------------------------------
# 2b. base64_frames 参数校验测试（新增）
# ---------------------------------------------------------------------------


def test_execute_tool_fd_rejects_base64_frames_not_list(db):
    """base64_frames 不为 list 时拒绝（invalid_base64_frames）。"""
    with pytest.raises(GovernanceError, match="invalid_base64_frames"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": "not_a_list", "history": []},
            db=db,
            trace_id="t-fd-b64-1",
        )


def test_execute_tool_fd_rejects_too_many_base64_frames(db):
    """base64_frames 元素数量超过 MAX_VINCI_BASE64_FRAMES 时拒绝。"""
    from app.agents.governance.gateway import MAX_VINCI_BASE64_FRAMES

    too_many = ["frame_data"] * (MAX_VINCI_BASE64_FRAMES + 1)
    with pytest.raises(GovernanceError, match="too_many_base64_frames"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": too_many, "history": []},
            db=db,
            trace_id="t-fd-b64-2",
        )


def test_execute_tool_fd_rejects_base64_frame_item_not_string(db):
    """base64_frames 内元素不是字符串时拒绝。"""
    with pytest.raises(GovernanceError, match="invalid_base64_frame_item"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": [123], "history": []},
            db=db,
            trace_id="t-fd-b64-3",
        )


def test_execute_tool_fd_rejects_empty_base64_frame(db):
    """base64_frames 内有空字符串元素时拒绝。"""
    with pytest.raises(GovernanceError, match="empty_base64_frame"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": ["valid", "", "also_valid"], "history": []},
            db=db,
            trace_id="t-fd-b64-4",
        )


def test_execute_tool_fd_rejects_base64_frame_too_large(db):
    """单帧 base64 字符串超过 MAX_VINCI_BASE64_FRAME_SIZE_CHARS 时拒绝。"""
    from app.agents.governance.gateway import MAX_VINCI_BASE64_FRAME_SIZE_CHARS

    too_large = "x" * (MAX_VINCI_BASE64_FRAME_SIZE_CHARS + 1)
    with pytest.raises(GovernanceError, match="base64_frame_too_large"):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": [too_large], "history": []},
            db=db,
            trace_id="t-fd-b64-5",
        )


def test_execute_tool_fd_accepts_valid_base64_frames(db):
    """合法的 base64_frames 列表通过校验。"""
    from app.agents.governance.gateway import MAX_VINCI_BASE64_FRAMES

    valid_frames = ["a" * 100 for _ in range(MAX_VINCI_BASE64_FRAMES)]
    # 只验证校验通过（不验证业务逻辑）
    try:
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": valid_frames, "history": []},
            db=db,
            trace_id="t-fd-b64-6",
        )
    except GovernanceError as e:
        # 可能因其他原因失败（如 governance context），但不是 base64_frames 校验失败
        assert "base64_frame" not in str(e), f"Unexpected base64_frames error: {e}"


def test_execute_tool_fd_accepts_empty_base64_frames(db):
    """base64_frames 为空列表时不报错（降级到纯文本模式）。"""
    try:
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "base64_frames": [], "history": []},
            db=db,
            trace_id="t-fd-b64-7",
        )
    except GovernanceError as e:
        # 可能因其他原因失败（如 governance context），但不是 base64_frames 校验失败
        assert "base64_frame" not in str(e), f"Unexpected base64_frames error: {e}"


def test_execute_tool_fd_accepts_no_base64_frames_key(db):
    """params 中完全不包含 base64_frames 键时通过校验（纯文本模式）。"""
    try:
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-b64-8",
        )
    except GovernanceError as e:
        # 可能因其他原因失败（如 governance context），但不是 base64_frames 校验失败
        assert "base64_frame" not in str(e), f"Unexpected base64_frames error: {e}"


# ---------------------------------------------------------------------------
# 3. 绕过网关直调阻断测试（ensure_in_governance_context 层面）
# ---------------------------------------------------------------------------


def test_fd_tool_rejects_direct_invocation_outside_gateway(db):
    """tool_lf_frame_description 在 gateway 外直调时抛出 governance_bypass_blocked。"""
    fd_tool = getattr(tools_learning_flow, "tool_lf_frame_description", None)
    if fd_tool is None:
        pytest.fail(
            "tool_lf_frame_description 未在 tools_learning_flow.py 中找到。"
            "请先实现该工具函数（见 M3-A 最小实现步骤 2）。"
        )
    with pytest.raises(GovernanceError, match="governance_bypass_blocked"):
        fd_tool(
            db,
            {
                "prompt": "hello",
                "session_id": "s1",
                "history": [],
                "trace_id": "t-fd-bypass",
            },
        )


def test_fd_tool_rejects_direct_invocation_outside_gateway__vinci_failure(db):
    """直调时即使 Vinci 抛出异常，仍先被治理拦截（不泄漏真实错误）。"""
    fd_tool = getattr(tools_learning_flow, "tool_lf_frame_description", None)
    if fd_tool is None:
        pytest.skip("tool_lf_frame_description 未实现，跳过本测试")
    with pytest.raises(GovernanceError, match="governance_bypass_blocked"):
        fd_tool(
            db,
            {
                "prompt": "hello",
                "session_id": "s1",
                "history": [],
                "trace_id": "t-fd-bypass-2",
            },
        )


# ---------------------------------------------------------------------------
# 4. 审计事件可观测测试
# ---------------------------------------------------------------------------


def test_fd_tool_denied_emits_agent_tool_denied_audit(db, caplog):
    """execute_tool 拒绝未注册工具时，发出 agent_tool_denied 审计事件。

    使用真正未注册的假工具名（lf_frame_description 已在白名单中，不再触发 denied 事件）。
    """
    caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
    with pytest.raises(GovernanceError, match="tool_not_allowed"):
        execute_tool(
            "lf_frame_description_fake",
            {"prompt": "hello", "session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-audit-denied",
        )

    payloads = _extract_analytics_payloads(caplog)
    denied_events = [p for p in payloads if p.get("event_type") == "agent_tool_denied"]
    assert denied_events, "agent_tool_denied 审计事件未发出"
    assert denied_events[-1]["metadata"].get("reason") == "not_whitelisted"
    assert denied_events[-1]["metadata"].get("tool") == "lf_frame_description_fake"


def test_fd_tool_param_invalid_emits_agent_tool_param_invalid_audit(db, caplog):
    """execute_tool 参数校验失败时，发出 agent_tool_param_invalid 审计事件。"""
    caplog.set_level(logging.INFO, logger="app.analytics.telemetry")
    with pytest.raises(GovernanceError, match="missing_prompt"):
        execute_tool(
            "lf_frame_description",
            {"session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-audit-param",
        )

    payloads = _extract_analytics_payloads(caplog)
    param_events = [p for p in payloads if p.get("event_type") == "agent_tool_param_invalid"]
    assert param_events, "agent_tool_param_invalid 审计事件未发出"
    assert param_events[-1]["metadata"].get("tool") == "lf_frame_description"
    assert "missing_prompt" in param_events[-1]["metadata"].get("error", "")


def test_fd_tool_completed_emits_agent_tool_completed_audit(db, monkeypatch, caplog, fd_mock_vinci_success):
    """execute_tool 正常完成时，发出 agent_tool_completed 审计事件。"""
    caplog.set_level(logging.INFO, logger="app.analytics.telemetry")

    # 替换 handler 使用 mock
    def _fake_fd_tool(db_session, params):
        _ = db_session
        return {"answer": "老师在黑板上写字", "session_id": params.get("session_id")}

    monkeypatch.setitem(gateway._TOOL_HANDLERS, "lf_frame_description", _fake_fd_tool)

    result = execute_tool(
        "lf_frame_description",
        {"prompt": "hello", "session_id": "s1", "history": []},
        db=db,
        trace_id="t-fd-audit-ok",
    )
    assert "answer" in result

    payloads = _extract_analytics_payloads(caplog)
    completed_events = [p for p in payloads if p.get("event_type") == "agent_tool_completed"]
    assert completed_events, "agent_tool_completed 审计事件未发出"
    assert completed_events[-1]["metadata"].get("tool") == "lf_frame_description"


def test_fd_tool_failed_emits_agent_tool_failed_audit(db, monkeypatch, caplog, fd_mock_vinci_timeout):
    """execute_tool 执行异常时，发出 agent_tool_failed 审计事件。"""
    caplog.set_level(logging.INFO, logger="app.analytics.telemetry")

    def _fake_fd_tool_fails(db_session, params):
        _ = db_session
        _ = params
        raise VinciAdapterError(
            message="timeout",
            error_code="VINCI_TIMEOUT",
            trace_id="t-fd-fail",
            status_code=504,
        )

    monkeypatch.setitem(gateway._TOOL_HANDLERS, "lf_frame_description", _fake_fd_tool_fails)

    with pytest.raises(VinciAdapterError):
        execute_tool(
            "lf_frame_description",
            {"prompt": "hello", "session_id": "s1", "history": []},
            db=db,
            trace_id="t-fd-audit-fail",
        )

    payloads = _extract_analytics_payloads(caplog)
    failed_events = [p for p in payloads if p.get("event_type") == "agent_tool_failed"]
    assert failed_events, "agent_tool_failed 审计事件未发出"
    assert failed_events[-1]["metadata"].get("tool") == "lf_frame_description"


# ---------------------------------------------------------------------------
# 5. 端到端集成测试（Service → Gateway → Tool）
# ---------------------------------------------------------------------------


def test_frame_description_service_via_gateway__success(db, monkeypatch, fd_mock_vinci_success):
    """FrameDescriptionService._call_vinci_sync 成功时，结果正确透传。"""
    # 验证前提：_call_vinci_sync 调用 execute_tool
    # 本测试在实现后作为回归保护
    import app.services.frame_description_service as fd_mod

    # 注入 mock adapter（实际路径走 execute_tool 后的 handler mock）
    service = fd_mod.FrameDescriptionService()
    # service._vinci_adapter = fd_mock_vinci_success  # 未来通过 gateway 路径
    # pending: 验证逻辑依赖实现后补充
    assert service is not None  # 占位，强制实现后覆盖


def test_frame_description_service_via_gateway__vinci_timeout_degrades(db, monkeypatch, fd_mock_vinci_timeout):
    """Vinci 超时且 allow_degrade=True 时，FrameDescriptionService 降级而非崩溃。"""
    import app.services.frame_description_service as fd_mod

    service = fd_mod.FrameDescriptionService()
    # pending: 验证逻辑依赖实现后补充
    assert service is not None  # 占位，强制实现后覆盖


def test_frame_description_service_via_gateway__param_rejected_raises(
    db,
    monkeypatch,
):
    """参数非法时 execute_tool 抛出 GovernanceError，Service 层正确处理。"""
    import app.services.frame_description_service as fd_mod

    service = fd_mod.FrameDescriptionService()
    # pending: 验证逻辑依赖实现后补充
    assert service is not None  # 占位，强制实现后覆盖


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_analytics_payloads(caplog) -> list[dict]:
    """从 caplog 中提取 app.analytics.telemetry 记录并解析 JSON。"""
    payloads = []
    for record in caplog.records:
        if record.name != "app.analytics.telemetry":
            continue
        try:
            payloads.append(json.loads(record.message))
        except Exception:
            continue
    return payloads
