"""Ollama 运行时单元测试。"""

import pytest
from app.core.config import settings
from app.services.ollama_runtime import get_ollama_runtime_status


@pytest.mark.unit
def test_get_ollama_runtime_status_when_service_available(monkeypatch):
    """Ollama 可用时应返回模型列表和当前配置模型。"""

    class FakeResponse:
        status_code = 200
        content = b'{"models":[{"name":"qwen-3.5:9b"}]}'

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"models": [{"name": "qwen-3.5:9b"}]}

    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434/api")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen-3.5:9b")
    monkeypatch.setattr("app.services.ollama_runtime.requests.get", lambda *args, **kwargs: FakeResponse())

    status = get_ollama_runtime_status()

    assert status["configured"] is True
    assert status["available"] is True
    assert status["model"] == "qwen-3.5:9b"
    assert status["model_present"] is True
    assert status["models"] == ["qwen-3.5:9b"]
    assert status["last_error"] == ""


@pytest.mark.unit
def test_get_ollama_runtime_status_when_service_unavailable(monkeypatch):
    """Ollama 不可用时应返回错误但不抛异常。"""
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434/api")
    monkeypatch.setattr(
        "app.services.ollama_runtime.requests.get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    )

    status = get_ollama_runtime_status()

    assert status["configured"] is True
    assert status["available"] is False
    assert status["model_present"] is False
    assert status["models"] == []
    assert "down" in status["last_error"]
