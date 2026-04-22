"""问答 API 测试。"""

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
