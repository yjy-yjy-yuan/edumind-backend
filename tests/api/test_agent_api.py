import pytest

from app.agents.exceptions import GovernanceError
from app.agents.governance import gateway
from app.agents.governance.context import ensure_in_governance_context
from app.core.config import settings
from app.models.note import Note, NoteTimestamp
from app.models.qa import Question
from app.models.subtitle import Subtitle
from app.models.video import VideoStatus


@pytest.mark.api
def test_execute_agent_creates_note_and_timestamp(client, db, sample_video):
    sample_video.status = VideoStatus.COMPLETED
    sample_video.summary = "视频摘要"
    db.add(
        Subtitle(
            video_id=sample_video.id,
            start_time=240.0,
            end_time=255.0,
            text="这一段讲的是导数的几何意义。",
            source="asr",
            language="zh",
        )
    )
    db.commit()

    response = client.post(
        "/api/agent/execute",
        json={
            "video_id": sample_video.id,
            "page_context": "video_detail",
            "current_time_seconds": 245,
            "subtitle_text": "这一段讲的是导数的几何意义。",
            "recent_qa_messages": [],
            "user_input": "把这一段记成笔记并总结一下",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "timestamp_note"
    assert "note_created" in payload["actions"]
    assert "timestamp_attached" in payload["actions"]
    assert payload["result"]["note_id"]
    assert payload["result"]["category"] in {
        "知识点",
        "例题",
        "思考题",
        "易错点",
        "结论",
    }
    meta = payload["result"].get("pipeline_meta") or {}
    assert meta.get("orchestration") == "planner_executor_validator_v1"
    assert meta.get("prompt_version") == "learning_flow_v1"
    assert "token_budget" in meta

    note = db.query(Note).filter(Note.id == payload["result"]["note_id"]).first()
    assert note is not None
    assert note.video_id == sample_video.id
    assert "把这一段记成笔记并总结一下" not in (note.content or "")
    assert "这一段讲的是导数的几何意义" in (note.content or "")
    assert note.title.endswith(" · 04:05")
    assert note.title.split(" · ")[0] in {"知识点", "例题", "思考题", "易错点", "结论"}
    assert db.query(NoteTimestamp).filter(NoteTimestamp.note_id == note.id).count() == 1
    assert db.query(Question).count() == 0


@pytest.mark.api
def test_execute_agent_rejects_missing_video(client):
    response = client.post(
        "/api/agent/execute",
        json={
            "video_id": 9999,
            "page_context": "qa",
            "current_time_seconds": 12,
            "subtitle_text": "字幕",
            "recent_qa_messages": [],
            "user_input": "保存为笔记",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "视频不存在"


@pytest.mark.api
def test_execute_agent_governance_error_returns_400(client, monkeypatch):
    def _raise_governance(*args, **kwargs):
        raise GovernanceError("governance_invalid_param")

    monkeypatch.setattr("app.routers.agent.execute_learning_flow_agent", _raise_governance)

    response = client.post(
        "/api/agent/execute",
        json={
            "video_id": 1,
            "page_context": "video_detail",
            "current_time_seconds": 0,
            "subtitle_text": "x",
            "recent_qa_messages": [],
            "user_input": "test",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "governance_invalid_param"


@pytest.mark.api
def test_execute_agent_budget_exceeded_returns_400(client, sample_video, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_LEARNING_FLOW_TOKEN_BUDGET", 50)

    response = client.post(
        "/api/agent/execute",
        json={
            "video_id": sample_video.id,
            "page_context": "video_detail",
            "current_time_seconds": 245,
            "subtitle_text": "字幕",
            "recent_qa_messages": [],
            "user_input": "把这一段记成笔记",
        },
    )

    assert response.status_code == 400
    assert "token_budget_exceeded" in response.json()["detail"]


@pytest.mark.api
def test_execute_agent_with_vinci_keeps_response_contract(client, db, sample_video, monkeypatch):
    """Vinci 接入后仍保持既有 agent 响应契约，不破坏前端字段。"""
    sample_video.status = VideoStatus.COMPLETED
    sample_video.summary = "导数课程摘要"
    db.add(
        Subtitle(
            video_id=sample_video.id,
            start_time=100.0,
            end_time=110.0,
            text="导数体现函数变化率。",
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
            "answer": "Vinci 总结：导数用于刻画变化率。",
            "history": [{"role": "assistant", "content": "Vinci 总结：导数用于刻画变化率。"}],
            "session_id": params.get("session_id", "sess-api"),
            "trace_id": params.get("trace_id", "trace-api"),
        }

    monkeypatch.setitem(gateway._TOOL_HANDLERS, "lf_vinci_chat", _fake_vinci_chat)

    response = client.post(
        "/api/agent/execute",
        json={
            "video_id": sample_video.id,
            "page_context": "video_detail",
            "current_time_seconds": 103,
            "subtitle_text": "导数体现函数变化率。",
            "recent_qa_messages": [{"role": "user", "content": "导数是什么"}],
            "user_input": "请结合 Vinci 给出学习笔记",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    # 兼容既有契约
    for field in (
        "intent",
        "plan",
        "actions",
        "result",
        "note_id",
        "video_id",
        "created_at",
        "action_records",
    ):
        assert field in payload
    # 新增 Vinci 行为应可观测
    assert "vinci_summary_generated" in payload["actions"]
