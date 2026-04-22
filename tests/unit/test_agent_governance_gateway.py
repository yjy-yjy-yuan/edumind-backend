"""智能体治理网关单测。"""

import json
import logging

import pytest

from app.agents.exceptions import GovernanceError
from app.agents.governance import gateway, tools_learning_flow
from app.agents.governance.gateway import execute_tool
from app.agents.governance.tools_learning_flow import tool_lf_generate_summary_fallback


def test_execute_tool_rejects_unknown_tool(db):
    with pytest.raises(GovernanceError, match="tool_not_allowed"):
        execute_tool("not_a_real_tool", {}, db=db, trace_id="t1")


def test_execute_tool_rejects_invalid_params_for_persist_note(db):
    with pytest.raises(GovernanceError, match="missing_title"):
        execute_tool(
            "lf_persist_note",
            {"video_id": 1, "content": "x", "title": ""},
            db=db,
            trace_id="t2",
        )


def test_execute_tool_summary_requires_seed(db):
    with pytest.raises(GovernanceError, match="missing_summary_seed"):
        execute_tool("lf_generate_summary_fallback", {}, db=db, trace_id="t3")


def test_execute_tool_rejects_invalid_note_type(db):
    with pytest.raises(GovernanceError, match="invalid_note_type"):
        execute_tool(
            "lf_persist_note",
            {"video_id": 1, "title": "t", "content": "c", "note_type": "evil"},
            db=db,
            trace_id="t4",
        )


def test_execute_tool_rejects_invalid_summary_style(db):
    with pytest.raises(GovernanceError, match="invalid_summary_style"):
        execute_tool(
            "lf_generate_summary_fallback",
            {"summary_seed": "x", "style": "not_a_style"},
            db=db,
            trace_id="t5",
        )


def test_tools_reject_direct_invocation_outside_gateway(db):
    with pytest.raises(GovernanceError, match="governance_bypass_blocked"):
        tool_lf_generate_summary_fallback(db, {"summary_seed": "x"})


def test_execute_tool_vinci_chat_requires_prompt(db):
    with pytest.raises(GovernanceError, match="missing_prompt"):
        execute_tool(
            "lf_vinci_chat",
            {"session_id": "s1", "history": []},
            db=db,
            trace_id="tv1",
        )


def test_execute_tool_vinci_chat_rejects_invalid_history_type(db):
    with pytest.raises(GovernanceError, match="invalid_history"):
        execute_tool(
            "lf_vinci_chat",
            {"prompt": "hello", "session_id": "s2", "history": "not_a_list"},
            db=db,
            trace_id="tv2",
        )


def test_execute_tool_vinci_chat_emits_completed_audit(db, monkeypatch, caplog):
    def _fake_vinci_chat(db_session, params):
        _ = db_session
        return {
            "answer": "ok",
            "session_id": params.get("session_id"),
            "history": [{"role": "assistant", "content": "ok"}],
            "trace_id": params.get("trace_id"),
        }

    monkeypatch.setitem(gateway._TOOL_HANDLERS, "lf_vinci_chat", _fake_vinci_chat)
    caplog.set_level(logging.INFO, logger="app.analytics.telemetry")

    result = execute_tool(
        "lf_vinci_chat",
        {"prompt": "hello", "session_id": "s3", "history": []},
        db=db,
        trace_id="tv3",
    )
    assert result["answer"] == "ok"

    payloads = []
    for record in caplog.records:
        if record.name != "app.analytics.telemetry":
            continue
        try:
            payloads.append(json.loads(record.message))
        except Exception:
            continue
    completed = [p for p in payloads if p.get("event_type") == "agent_tool_completed"]
    assert completed
    assert completed[-1]["metadata"].get("tool") == "lf_vinci_chat"


def test_vinci_tool_rejects_direct_invocation_outside_gateway(db):
    vinci_tool = getattr(tools_learning_flow, "tool_lf_vinci_chat", None)
    if vinci_tool is None:
        pytest.fail("缺少 tool_lf_vinci_chat，无法验证治理绕过阻断")
    with pytest.raises(GovernanceError, match="governance_bypass_blocked"):
        vinci_tool(
            db,
            {
                "prompt": "hello",
                "session_id": "s4",
                "history": [],
                "trace_id": "tv4",
            },
        )
