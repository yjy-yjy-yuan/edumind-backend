"""聊天系统降级测试。"""

import asyncio

import pytest

from app.utils.chat_system import get_chat_response, get_chat_response_async
from app.utils.qa_utils import QAProviderError


@pytest.mark.unit
def test_chat_response_returns_fallback_when_provider_fails(monkeypatch):
    def fake_call(messages, *, provider, model):
        _ = (messages, provider, model)
        raise QAProviderError("upstream timeout")

    monkeypatch.setattr("app.utils.chat_system.call_provider_chat", fake_call)

    result = get_chat_response([{"role": "user", "content": "解释一下导数"}])

    assert "当前模型服务繁忙" in result["content"]
    assert result["provider"] == "qwen"


@pytest.mark.unit
def test_chat_response_async_uses_async_provider(monkeypatch):
    async def fake_call(messages, *, provider, model):
        _ = (messages, provider, model)
        return "异步回答"

    monkeypatch.setattr("app.utils.chat_system.call_provider_chat_async", fake_call)

    result = asyncio.run(get_chat_response_async([{"role": "user", "content": "解释一下导数"}]))

    assert result["content"] == "异步回答"
    assert result["provider"] == "qwen"
