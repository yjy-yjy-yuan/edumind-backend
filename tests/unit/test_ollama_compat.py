"""Ollama 兼容工具单元测试。"""

import pytest
from app.utils.ollama_compat import OLLAMA_STOP_TOKENS
from app.utils.ollama_compat import build_ollama_options
from app.utils.ollama_compat import sanitize_ollama_response_text


@pytest.mark.unit
def test_build_ollama_options_includes_default_stop_tokens():
    """默认 Ollama 选项应带上 stop tokens，避免模型续写下一轮对话。"""
    options = build_ollama_options(temperature=0.3, num_predict=256)

    assert options["stop"] == OLLAMA_STOP_TOKENS
    assert options["temperature"] == 0.3
    assert options["num_predict"] == 256


@pytest.mark.unit
def test_sanitize_ollama_response_text_removes_reasoning_markup():
    """应清理 think/analysis 标签和常见控制 token。"""
    raw = (
        "<think>internal reasoning</think>\n"
        "最终答案<|endoftext|>\n"
        "<|im_start|>assistant\n"
        "<analysis>more reasoning</analysis>\n"
    )

    sanitized = sanitize_ollama_response_text(raw)

    assert sanitized == "最终答案"
