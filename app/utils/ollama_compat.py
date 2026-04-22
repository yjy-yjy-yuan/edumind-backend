"""Ollama 输出兼容工具。"""

from __future__ import annotations

import re
from typing import Any

OLLAMA_STOP_TOKENS = ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]

_REASONING_BLOCK_RE = re.compile(r"<(?:think|analysis)>[\s\S]*?</(?:think|analysis)>", re.IGNORECASE)
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^>]+?\|>")
_STRAY_REASONING_TAG_RE = re.compile(r"</?(?:think|analysis)>", re.IGNORECASE)
_ROLE_LINE_RE = re.compile(r"(?mi)^\s*(assistant|user|system)\s*$")


def build_ollama_options(**overrides: Any) -> dict[str, Any]:
    """构建带默认 stop token 的 Ollama 选项。"""
    options: dict[str, Any] = {"stop": OLLAMA_STOP_TOKENS.copy()}
    options.update(overrides)
    return options


def sanitize_ollama_response_text(text: str) -> str:
    """去除部分推理模型常见的思维标签和控制 token。"""
    sanitized = str(text or "")
    sanitized = _REASONING_BLOCK_RE.sub("", sanitized)
    sanitized = _SPECIAL_TOKEN_RE.sub("", sanitized)
    sanitized = _STRAY_REASONING_TAG_RE.sub("", sanitized)
    sanitized = _ROLE_LINE_RE.sub("", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()
