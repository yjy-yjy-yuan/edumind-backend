"""Ollama 运行时探测工具。"""

from __future__ import annotations

import logging
from typing import Any

import requests
from app.core.config import settings

logger = logging.getLogger(__name__)


def _model_matches(configured_model: str, loaded_model: str) -> bool:
    """兼容 Ollama 默认把未显式标注 tag 的模型解析为 :latest。"""
    configured = str(configured_model or "").strip()
    loaded = str(loaded_model or "").strip()
    if not configured or not loaded:
        return False
    return loaded == configured or loaded == f"{configured}:latest"


def get_ollama_runtime_status(timeout: int = 3) -> dict[str, Any]:
    """返回当前 Ollama 运行时状态，供健康检查和兼容能力展示使用。"""
    status: dict[str, Any] = {
        "configured": bool(str(settings.OLLAMA_BASE_URL or "").strip()),
        "available": False,
        "base_url": str(settings.OLLAMA_BASE_URL or "").strip(),
        "model": str(settings.OLLAMA_MODEL or "").strip(),
        "model_present": False,
        "models": [],
        "last_error": "",
    }

    if not status["configured"]:
        status["last_error"] = "OLLAMA_BASE_URL 未配置"
        return status

    try:
        response = requests.get(f"{settings.OLLAMA_BASE_URL}/tags", timeout=timeout)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        models = payload.get("models") if isinstance(payload, dict) else []
        status["available"] = True
        status["models"] = [
            str(item.get("name", "")).strip()
            for item in models
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        status["model_present"] = any(_model_matches(status["model"], model_name) for model_name in status["models"])
        return status
    except Exception as exc:
        status["last_error"] = str(exc)
        logger.info("Ollama 运行时暂不可用 | error=%s", exc)
        return status
