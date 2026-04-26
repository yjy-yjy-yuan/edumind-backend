"""RAG 问答工具测试。"""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.utils.qa_utils import QASystem


def build_video_stub():
    return SimpleNamespace(
        title="导数课程",
        summary="本节课讲解导数定义、几何意义和切线斜率。",
        tags='["导数", "切线斜率"]',
        subtitle_filepath="",
        subtitles=[
            SimpleNamespace(start_time=0.0, end_time=12.0, text="首先回顾导数定义和极限思想。"),
            SimpleNamespace(start_time=12.0, end_time=28.0, text="导数的几何意义是函数图像在某一点处切线的斜率。"),
            SimpleNamespace(start_time=28.0, end_time=42.0, text="随后通过例题说明如何计算切线斜率。"),
        ],
    )


@pytest.mark.unit
def test_qasystem_returns_references_from_relevant_chunks(monkeypatch):
    captured = {}

    def fake_call(messages, *, provider, model):
        captured["messages"] = messages
        captured["provider"] = provider
        captured["model"] = model
        return "根据字幕，导数的几何意义是曲线在该点切线的斜率。[1]"

    monkeypatch.setattr("app.utils.qa_utils.call_provider_chat", fake_call)

    result = QASystem(video=build_video_stub()).ask("导数的几何意义是什么？", provider="qwen", mode="video")

    assert result["provider"] == "qwen"
    assert result["model"] == settings.QWEN_QA_MODEL
    assert result["references"]
    assert any("几何意义" in item["preview"] for item in result["references"])
    assert "检索到的上下文如下" in captured["messages"][1]["content"]
    assert captured["provider"] == "qwen"


@pytest.mark.unit
def test_qasystem_uses_deepseek_reasoner_when_deep_thinking(monkeypatch):
    captured = {}

    def fake_call(messages, *, provider, model):
        captured["provider"] = provider
        captured["model"] = model
        return "这是 DeepSeek 的回答。"

    monkeypatch.setattr("app.utils.qa_utils.call_provider_chat", fake_call)

    QASystem(video=build_video_stub()).ask(
        "请总结这节课。",
        provider="deepseek",
        deep_thinking=True,
        mode="video",
    )

    assert captured["provider"] == "deepseek"
    assert captured["model"] == settings.DEEPSEEK_REASONER_MODEL
