"""学习流 pipeline 与 Vinci 接入链路测试（M2）。"""

from __future__ import annotations

from app.agents.exceptions import GovernanceError
from app.agents.governance import gateway
from app.agents.governance.context import ensure_in_governance_context
from app.agents.pipelines.learning_flow_pipeline import run_learning_flow_pipeline
from app.core.config import settings
from app.models.subtitle import Subtitle
from app.models.video import VideoStatus
from app.schemas.agent import AgentExecuteRequest


def test_learning_flow_pipeline_vinci_call_must_go_through_governance_gateway(db, sample_video, monkeypatch):
    """Vinci 调用必须进入 execute_tool -> governance gateway，而不是直接调用。"""
    sample_video.status = VideoStatus.COMPLETED
    sample_video.summary = "导数定义与几何意义"
    db.add(
        Subtitle(
            video_id=sample_video.id,
            start_time=30.0,
            end_time=45.0,
            text="导数的几何意义是切线斜率。",
            source="asr",
            language="zh",
        )
    )
    db.commit()

    monkeypatch.setattr(settings, "VINCI_ENABLED", True)

    def _fake_vinci_chat(db_session, params):
        _ = db_session
        ensure_in_governance_context()
        return {
            "answer": "Vinci 摘要：导数描述函数变化率。",
            "history": [{"role": "assistant", "content": "Vinci 摘要：导数描述函数变化率。"}],
            "session_id": params.get("session_id", "sess-test"),
            "trace_id": params.get("trace_id", "trace-test"),
        }

    monkeypatch.setitem(gateway._TOOL_HANDLERS, "lf_vinci_chat", _fake_vinci_chat)

    called_tools: list[str] = []

    def _spy_execute_tool(tool_name, params, *, db, trace_id, pipeline="learning_flow"):
        called_tools.append(tool_name)
        return gateway.execute_tool(tool_name, params, db=db, trace_id=trace_id, pipeline=pipeline)

    monkeypatch.setattr("app.agents.pipelines.learning_flow_pipeline.execute_tool", _spy_execute_tool)

    payload = run_learning_flow_pipeline(
        db,
        request=AgentExecuteRequest(
            video_id=sample_video.id,
            page_context="video_detail",
            current_time_seconds=35.0,
            subtitle_text="导数的几何意义是切线斜率",
            recent_qa_messages=[{"role": "user", "content": "导数是什么"}],
            user_input="请用 Vinci 总结后再生成笔记",
        ),
    )

    assert "lf_vinci_chat" in called_tools
    assert payload["result"]["summary"].startswith("Vinci 摘要：")


def test_learning_flow_pipeline_continues_when_vinci_timeout_with_fallback(db, sample_video, monkeypatch):
    """Vinci 超时时主流程应降级继续，回退本地摘要并完成笔记写入。"""
    sample_video.status = VideoStatus.COMPLETED
    sample_video.summary = "函数极值与导数判别法"
    db.add(
        Subtitle(
            video_id=sample_video.id,
            start_time=10.0,
            end_time=25.0,
            text="利用导数符号变化可以判断函数极值。",
            source="asr",
            language="zh",
        )
    )
    db.commit()

    monkeypatch.setattr(settings, "VINCI_ENABLED", True)

    def _timeout_vinci_chat(db_session, params):
        _ = (db_session, params)
        raise GovernanceError("vinci_call_failed:VINCI_TIMEOUT")

    monkeypatch.setitem(gateway._TOOL_HANDLERS, "lf_vinci_chat", _timeout_vinci_chat)

    payload = run_learning_flow_pipeline(
        db,
        request=AgentExecuteRequest(
            video_id=sample_video.id,
            page_context="video_detail",
            current_time_seconds=12.0,
            subtitle_text="利用导数符号变化可以判断函数极值",
            recent_qa_messages=[{"role": "user", "content": "怎么判断极值"}],
            user_input="请用 Vinci 总结后帮我生成笔记",
        ),
    )

    actions = payload["actions"]
    assert "vinci_degraded_fallback" in actions
    assert "summary_generated" in actions
    assert "note_created" in actions
    assert payload["result"]["note_id"]
    assert str(payload["result"]["summary"] or "").strip()
