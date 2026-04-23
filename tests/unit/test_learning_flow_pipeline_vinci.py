"""学习流 pipeline 与 Vinci 接入链路测试（M2）。"""

from __future__ import annotations

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
