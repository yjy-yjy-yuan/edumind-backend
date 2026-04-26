"""问答 API 测试。"""

import json

import pytest

from app.models.qa import Question
from app.models.subtitle import Subtitle


@pytest.mark.api
def test_ask_question_uses_video_rag_pipeline(client, db, sample_video, monkeypatch):
    sample_video.title = "导数课程"
    sample_video.summary = "本节课重点讲导数的几何意义。"
    db.add(
        Subtitle(
            video_id=sample_video.id,
            start_time=10.0,
            end_time=24.0,
            text="导数的几何意义是函数图像在某一点处切线的斜率。",
            source="asr",
            language="zh",
        )
    )
    db.commit()

    monkeypatch.setattr(
        "app.utils.qa_utils.call_provider_chat",
        lambda messages, *, provider, model: "根据字幕，导数的几何意义是切线斜率。[1]",
    )

    response = client.post(
        "/api/qa/ask",
        json={
            "video_id": sample_video.id,
            "question": "导数的几何意义是什么？",
            "mode": "video",
            "provider": "qwen",
            "stream": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "切线斜率" in payload["answer"]
    assert payload["provider"] == "qwen"
    assert payload["references"]

    saved = db.query(Question).all()
    assert len(saved) == 1
    assert saved[0].video_id == sample_video.id
    assert "切线斜率" in saved[0].answer


@pytest.mark.api
def test_ask_question_rejects_video_without_context(client, sample_video):
    response = client.post(
        "/api/qa/ask",
        json={
            "video_id": sample_video.id,
            "question": "这节课讲了什么？",
            "mode": "video",
            "provider": "qwen",
            "stream": False,
        },
    )

    assert response.status_code == 400
    assert "暂无可用于问答的字幕或摘要内容" in response.json()["detail"]


@pytest.mark.api
def test_ask_question_stream_normalizes_final_answer_event(client, db, sample_video, monkeypatch):
    sample_video.title = "导数课程"
    sample_video.summary = "本节课重点讲导数。"
    db.add(
        Subtitle(
            video_id=sample_video.id,
            start_time=10.0,
            end_time=24.0,
            text="导数是函数图像在某一点处切线的斜率。",
            source="asr",
            language="zh",
        )
    )
    db.commit()

    def _fake_answer_stream(self, question, **kwargs):
        _ = (self, question, kwargs)
        yield {"type": "answer", "answer": "这是最终回答"}

    monkeypatch.setattr("app.utils.qa_utils.QASystem.answer_stream", _fake_answer_stream)

    response = client.post(
        "/api/qa/ask",
        json={
            "video_id": sample_video.id,
            "question": "导数是什么？",
            "mode": "video",
            "provider": "qwen",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")

    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events
    assert events[0]["type"] == "status"
    assert events[0]["stage"] == "accepted"
    answer_events = [event for event in events if event.get("type") == "answer"]
    assert answer_events
    final_answer = answer_events[-1]
    assert final_answer["stage"] == "completed"
    assert final_answer["progress"] == 100
    assert final_answer["message"] == "回答已完成"
    assert final_answer["provider"] == "qwen"
