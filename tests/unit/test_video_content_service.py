"""视频摘要与标签服务测试。"""

import pytest
from app.services.video_content_service import SUMMARY_STYLE_STUDY
from app.services.video_content_service import fallback_summary
from app.services.video_content_service import generate_video_summary
from app.services.video_content_service import generate_video_tags


@pytest.mark.unit
def test_generate_video_summary_falls_back_when_ai_unavailable(monkeypatch):
    transcript = (
        "这一节课程先介绍导数的定义和极限思想。"
        "接着说明导数的几何意义，以及切线斜率如何计算。"
        "随后总结常见求导法则，并通过例题展示复合函数求导。"
        "最后补充学习时容易混淆的几个概念。"
    )

    monkeypatch.setattr("app.services.video_content_service.call_online_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.video_content_service.call_ollama", lambda *args, **kwargs: None)

    result = generate_video_summary(
        1,
        transcript_text=transcript,
        title="导数专题",
        style=SUMMARY_STYLE_STUDY,
    )

    assert result["success"] is True
    assert "导数" in result["summary"]
    assert result["provider"] == "fallback"


@pytest.mark.unit
def test_generate_video_tags_falls_back_to_keywords(monkeypatch):
    summary = "主题：导数专题\n学习重点：\n1. 理解导数定义。\n2. 掌握几何意义。\n3. 熟悉常见求导法则。"

    monkeypatch.setattr("app.services.video_content_service.call_online_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.video_content_service.call_ollama", lambda *args, **kwargs: None)

    result = generate_video_tags(1, summary, title="导数专题", max_tags=5)

    assert result["success"] is True
    assert result["provider"] == "fallback"
    assert 1 <= len(result["tags"]) <= 5
    assert result["tags"][0] == "数学"
    assert any("导数" in tag for tag in result["tags"])


@pytest.mark.unit
def test_fallback_summary_returns_non_empty_text():
    transcript = "第一部分介绍函数。第二部分讲导数。第三部分通过例题讲应用。"
    summary = fallback_summary(transcript, title="高数复习", style=SUMMARY_STYLE_STUDY)

    assert isinstance(summary, str)
    assert summary.strip()
