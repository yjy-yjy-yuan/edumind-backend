"""智能体治理网关单测。"""

import pytest
from app.agents.exceptions import GovernanceError
from app.agents.governance.gateway import execute_tool


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
