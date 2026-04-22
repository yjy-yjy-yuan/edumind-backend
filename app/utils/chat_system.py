"""聊天系统工具 - FastAPI 版本"""

import logging
from typing import Generator
from typing import List

from app.utils.qa_utils import call_provider_chat
from app.utils.qa_utils import normalize_provider
from app.utils.qa_utils import resolve_model

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
    - direct: 优先使用通义千问，失败后自动切换到 DeepSeek 兜底
    - deep_think: 强制使用 deepseek-reasoner，不进行兜底
    """
    result = _execute_chat_with_fallback(messages, mode, provider, model)
    yield result


def get_chat_response(messages: List[dict], mode: str = "direct", provider: str = "qwen", model: str = "") -> dict:
    """在线模型聊天非流式响应。

    对话模式:
    - direct: 优先使用通义千问，失败后自动切换到 DeepSeek 兜底
    - deep_think: 强制使用 deepseek-reasoner，不进行兜底
    """
    content, final_provider, final_model = _execute_chat_with_fallback(messages, mode, provider, model)
    return {
        "content": content,
        "provider": final_provider,
        "model": final_model,
    }


def _execute_chat_with_fallback(messages: List[dict], mode: str, provider: str, model: str) -> tuple[str, str, str]:
    """执行聊天请求，支持模式化路由和兜底。

    Args:
        messages: 聊天消息列表
        mode: 对话模式 ("direct" | "deep_think")
        provider: 指定的 provider
        model: 指定的模型

    Returns:
        (content, final_provider, final_model) 元组
    """
    normalized_messages = normalize_chat_messages(messages)

    if mode == "deep_think":
        # 深度思考模式：强制使用 deepseek-reasoner，不兜底
        resolved_provider = "deepseek"
        resolved_model = resolve_model(resolved_provider, model, deep_thinking=True)
        logger.info("深度思考模式调用: provider=%s, model=%s", resolved_provider, resolved_model)
        content = call_provider_chat(normalized_messages, provider=resolved_provider, model=resolved_model)
        return content, resolved_provider, resolved_model

    # 直接回答模式：优先通义千问，兜底 DeepSeek
    # 1. 尝试通义千问
    primary_provider = normalize_provider(provider, model) or "qwen"
    primary_model = resolve_model(primary_provider, model, deep_thinking=False)

    # 如果用户指定了 deepseek，优先使用它
    if primary_provider == "deepseek":
        primary_provider = "qwen"
        primary_model = resolve_model("qwen", "", deep_thinking=False)
        logger.info("直接回答模式优先切换到通义千问: model=%s", primary_model)

    try:
        logger.info("直接回答模式-主模型: provider=%s, model=%s", primary_provider, primary_model)
        content = call_provider_chat(normalized_messages, provider=primary_provider, model=primary_model)
        return content, primary_provider, primary_model
    except Exception as primary_error:
        logger.warning("直接回答模式-主模型(%s)调用失败，触发兜底: %s", primary_model, primary_error)

    # 2. 兜底：切换到 DeepSeek
    fallback_provider = "deepseek"
    fallback_model = resolve_model(fallback_provider, "deepseek-chat", deep_thinking=False)

    try:
        logger.info("直接回答模式-兜底模型: provider=%s, model=%s", fallback_provider, fallback_model)
        content = call_provider_chat(normalized_messages, provider=fallback_provider, model=fallback_model)
        logger.info("直接回答模式-兜底成功: provider=%s, model=%s", fallback_provider, fallback_model)
        return content, fallback_provider, fallback_model
    except Exception as fallback_error:
        logger.error("直接回答模式-兜底模型(%s)也失败: %s", fallback_model, fallback_error)
        # 两个模型都失败，抛出原错误
        raise primary_error
