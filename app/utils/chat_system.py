"""聊天系统工具 - FastAPI 版本"""

import json
import logging
from typing import Generator, List

import requests

from app.utils.qa_utils import (call_provider_chat, normalize_provider,
                                resolve_model)

logger = logging.getLogger(__name__)


def normalize_chat_messages(messages: List[dict]) -> List[dict]:
    """将聊天消息统一转换为 OpenAI 兼容结构。"""
    normalized = []
    for item in messages or []:
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
        else:
            role = str(getattr(item, "role", "") or "").strip()
            content = str(getattr(item, "content", "") or "").strip()

        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def stream_chat(
    messages: List[dict], mode: str = "direct", provider: str = "qwen", model: str = ""
) -> Generator[str, None, None]:
    """在线模型聊天流式输出。当前以单次返回内容形式输出。

    对话模式:
    - direct: 使用通义千问
    - deep_think: 强制使用 deepseek-reasoner
    """
    content, final_provider, final_model = _execute_chat(
        messages, mode, provider, model
    )
    result = json.dumps(
        {"content": content, "provider": final_provider, "model": final_model},
        ensure_ascii=False,
    )
    yield f"{result}\n"


def get_chat_response(
    messages: List[dict], mode: str = "direct", provider: str = "qwen", model: str = ""
) -> dict:
    """在线模型聊天非流式响应。

    对话模式:
    - direct: 使用通义千问
    - deep_think: 强制使用 deepseek-reasoner
    """
    content, final_provider, final_model = _execute_chat(
        messages, mode, provider, model
    )
    return {
        "content": content,
        "provider": final_provider,
        "model": final_model,
    }


def _execute_chat(
    messages: List[dict], mode: str, provider: str, model: str
) -> tuple[str, str, str]:
    """执行聊天请求。

    Args:
        messages: 聊天消息列表
        mode: 对话模式 ("direct" | "deep_think")
        provider: 指定的 provider
        model: 指定的模型

    Returns:
        (content, provider, model) 元组
    """
    normalized_messages = normalize_chat_messages(messages)

    if mode == "deep_think":
        # 深度思考模式：强制使用 deepseek-reasoner
        resolved_provider = "deepseek"
        resolved_model = resolve_model(resolved_provider, model, deep_thinking=True)
        logger.info(
            "深度思考模式调用: provider=%s, model=%s", resolved_provider, resolved_model
        )
        content = call_provider_chat(
            normalized_messages, provider=resolved_provider, model=resolved_model
        )
        return content, resolved_provider, resolved_model

    # 直接回答模式：使用云端通义千问 API
    primary_provider = normalize_provider(provider, model) or "qwen"
    primary_model = resolve_model(primary_provider, model, deep_thinking=False)

    if primary_provider == "deepseek":
        primary_provider = "qwen"
        primary_model = resolve_model("qwen", "", deep_thinking=False)
        logger.info("直接回答模式优先切换到通义千问: model=%s", primary_model)

    logger.info(
        "直接回答模式-云端主模型: provider=%s, model=%s",
        primary_provider,
        primary_model,
    )
    content = call_provider_chat(
        normalized_messages, provider=primary_provider, model=primary_model
    )
    return content, primary_provider, primary_model
