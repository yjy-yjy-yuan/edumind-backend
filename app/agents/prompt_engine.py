"""统一提示词组装引擎。

三层缓存 + Token 感知截断：
- Tier-1（模板缓存）：已编译 Jinja2 模板按名称 LRU 缓存，避免重复编译
- Tier-2（Token 截断）：超出预算时按策略截断历史上下文（保留关键转折点）
- Tier-3（变量绑定）：运行时片段动态组合

集成路径：
- app/agents/budget.py     → TokenBudget（预算硬上限）
- app/analytics/pipeline.py → get_telemetry()（事件写入）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Tokenizer — 生产优先 tiktoken，跌级用字符估算
# ---------------------------------------------------------------

_TOKENIZER_CACHE: Dict[str, Any] = {}


def _get_tokenizer(encoding_name: str = "cl100k_base") -> Optional[Any]:
    """延迟加载 tiktoken；加载失败返回 None（降级字符估算）。"""
    if encoding_name in _TOKENIZER_CACHE:
        return _TOKENIZER_CACHE[encoding_name]

    try:
        import tiktoken

        enc = tiktoken.get_encoding(encoding_name)
        _TOKENIZER_CACHE[encoding_name] = enc
        return enc
    except Exception:
        logger.debug("tiktoken unavailable, falling back to char-count estimation")
        return None


@dataclass
class TokenBudget:
    """与 app.agents.budget.TokenBudget 接口对齐，提供 remaining 接口。"""

    max_tokens: int
    used_tokens: int = 0

    def charge(self, step: str, estimated_tokens: int) -> None:
        self.used_tokens += max(0, int(estimated_tokens))

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)


# ---------------------------------------------------------------
# Prompt Segment（提示词片段）
# ---------------------------------------------------------------


@dataclass
class PromptSegment:
    """可复用的提示词片段，支持模板变量插值。"""

    name: str
    content: str
    priority: int = 0  # 数值越大越靠前（系统角色通常 priority=100）
    variables: Tuple[str, ...] = field(default_factory=tuple)  # 变量名列表

    def render(self, **kwargs: Any) -> "PromptSegment":
        """渲染变量后返回新片段（自身不变）。"""
        rendered_content = self.content
        for var in self.variables:
            placeholder = "{{" + var + "}}"
            if placeholder in rendered_content:
                rendered_content = rendered_content.replace(placeholder, str(kwargs.get(var, "")))
        return PromptSegment(name=self.name, content=rendered_content, priority=self.priority)


@dataclass
class AssembledPrompt:
    """组装后的完整提示词。"""

    text: str
    token_count: int
    segments_used: Tuple[str, ...]  # 使用了哪些片段
    truncated: bool = False  # 是否发生过截断
    truncation_strategy: str = ""  # 截断策略说明
    budget_snapshot: Dict[str, int] = field(default_factory=dict)  # 当时的预算快照


# ---------------------------------------------------------------
# Truncation Strategies
# ---------------------------------------------------------------


class TruncationStrategy:
    """上下文截断策略抽象。"""

    def apply(
        self,
        messages: List[Dict[str, str]],
        available_tokens: int,
        tokenizer: Optional[Any],
    ) -> List[Dict[str, str]]:
        raise NotImplementedError


class PreserveMilestonesStrategy(TruncationStrategy):
    """保留首尾 + 关键转折点（以 [关键|转折|决策] 等关键词判定）。"""

    MILESTONE_PATTERNS = re.compile(
        r"(关键|转折|决策|注意|重要|总结|结论|因此|所以|但是|然而|不过|不过|总之|归纳)",
        re.IGNORECASE,
    )

    def apply(
        self,
        messages: List[Dict[str, str]],
        available_tokens: int,
        tokenizer: Optional[Any],
    ) -> List[Dict[str, str]]:
        if len(messages) <= 2:
            return messages[:]

        def _tokens(m: Dict[str, str]) -> int:
            content = str(m.get("content") or "")
            if tokenizer is not None:
                return len(tokenizer.encode(content))
            return max(1, len(content) // 4 + 1)

        total = sum(_tokens(m) for m in messages)
        if total <= available_tokens:
            return messages

        kept: List[Dict[str, str]] = []
        milestone_indices: List[int] = []

        for i, msg in enumerate(messages):
            if self.MILESTONE_PATTERNS.search(str(msg.get("content") or "")):
                milestone_indices.append(i)

        system = messages[0] if messages and messages[0].get("role") == "system" else None
        last = messages[-1] if messages else None

        core = [m for i, m in enumerate(messages) if i in milestone_indices]

        # 保留 token 计数（头 + 尾 + 里程碑）
        core_tokens = sum(_tokens(m) for m in core)
        header_tokens = _tokens(system) if system else 0
        footer_tokens = _tokens(last) if last else 0

        allowed_milestones = available_tokens - header_tokens - footer_tokens
        if allowed_milestones < 0:
            # 极端情况：只剩 system + last
            result: List[Dict[str, str]] = []
            if system:
                result.append(system)
            if last and last is not system:
                result.append(last)
            return result

        # 贪婪保留里程碑直到 token 超限
        selected: List[Dict[str, str]] = []
        selected_tokens = 0
        for m in core:
            t = _tokens(m)
            if selected_tokens + t <= allowed_milestones:
                selected.append(m)
                selected_tokens += t

        result = []
        if system:
            result.append(system)
        result.extend(selected)
        if last and last is not system and last not in selected:
            if selected_tokens + _tokens(last) <= allowed_milestones:
                result.append(last)

        return result


class HeadOnlyStrategy(TruncationStrategy):
    """简单策略：只保留最近 N 条消息。"""

    def __init__(self, keep_recent: int = 10):
        self.keep_recent = keep_recent

    def apply(
        self,
        messages: List[Dict[str, str]],
        available_tokens: int,
        tokenizer: Optional[Any],
    ) -> List[Dict[str, str]]:
        if len(messages) <= 2:
            return messages[:]

        def _tokens(m: Dict[str, str]) -> int:
            content = str(m.get("content") or "")
            if tokenizer is not None:
                return len(tokenizer.encode(content))
            return max(1, len(content) // 4 + 1)

        # 保留 system 头
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        tail = messages[1:] if system else messages

        # 从后向前保留直到 token 预算用完
        result = []
        if system:
            result.append(system)

        selected_from_tail: List[Dict[str, str]] = []
        selected_tokens = 0
        for msg in reversed(tail):
            t = _tokens(msg)
            if selected_tokens + t <= available_tokens:
                selected_from_tail.insert(0, msg)
                selected_tokens += t
            else:
                break

        result.extend(selected_from_tail)
        return result


# ---------------------------------------------------------------
# PromptEngine
# ---------------------------------------------------------------


@dataclass
class PromptEngineConfig:
    """PromptEngine 运行时配置。"""

    # tiktoken encoding 名称（默认 cl100k_base，GPT-4/Claude 均支持）
    tokenizer_encoding: str = "cl100k_base"
    # Tier-1 模板缓存容量
    template_cache_size: int = 256
    # 默认截断策略
    default_truncation_strategy: str = "preserve_milestones"  # preserve_milestones | head_only
    # 是否在超预算时抛异常（False=静默截断）
    raise_on_exceed: bool = False


class PromptEngine:
    """
    三层缓存 + Token 感知提示词组装引擎。

    使用示例::

        engine = PromptEngine(config=PromptEngineConfig())
        segments = [
            PromptSegment("system_role", "你是一个专业助手...", priority=100),
            PromptSegment("context", "当前视频：{{ video_title }}", variables=("video_title",)),
        ]
        result = engine.assemble(
            segments,
            variables={"video_title": "微积分入门"},
            messages=[...],
            token_budget=TokenBudget(max_tokens=8000, used_tokens=1200),
        )
    """

    def __init__(
        self,
        config: Optional[PromptEngineConfig] = None,
    ):
        self.config = config or PromptEngineConfig()
        self._tokenizer = _get_tokenizer(self.config.tokenizer_encoding)
        self._template_cache: Dict[str, PromptSegment] = {}
        self._cache_order: List[str] = []
        self._cache_hits = 0
        self._cache_misses = 0

    # ---- public API ----

    def register_segment(self, segment: PromptSegment) -> None:
        """将片段注册到 Tier-1 模板缓存。"""
        key = segment.name
        self._template_cache[key] = segment
        if key not in self._cache_order:
            self._cache_order.append(key)
        # 超过缓存上限时驱逐最老的
        while len(self._cache_order) > self.config.template_cache_size:
            oldest = self._cache_order.pop(0)
            self._template_cache.pop(oldest, None)

    def get_segment(self, name: str) -> Optional[PromptSegment]:
        """从 Tier-1 缓存读取片段（不命中则返回 None）。"""
        seg = self._template_cache.get(name)
        if seg is not None:
            self._cache_hits += 1
        else:
            self._cache_misses += 1
        return seg

    def assemble(
        self,
        segments: List[PromptSegment],
        *,
        variables: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        token_budget: Optional[TokenBudget] = None,
        truncation_strategy: Optional[str] = None,
        extra_system_prompt: str = "",
    ) -> AssembledPrompt:
        """
        组装完整提示词。

        参数:
            segments: 片段列表，按 priority 降序排列后拼接
            variables: 渲染片段变量
            messages: 历史消息（用于截断）
            token_budget: Token 预算（用于计算可用空间和硬上限）
            truncation_strategy: override 默认截断策略
            extra_system_prompt: 追加到系统提示词后的额外内容
        """
        variables = variables or {}
        strategy_name = truncation_strategy or self.config.default_truncation_strategy

        # Step 1: 渲染片段（Tier-2 变量绑定）
        rendered_segments = []
        for seg in sorted(segments, key=lambda s: s.priority, reverse=True):
            rendered_segments.append(seg.render(**variables))

        # Step 2: 合并为系统提示词
        system_parts = [seg.content for seg in rendered_segments]
        if extra_system_prompt.strip():
            system_parts.append(extra_system_prompt.strip())
        system_text = "\n\n".join(filter(None, system_parts))

        # Step 3: Token 计数
        system_tokens = self._count_tokens(system_text)
        budget_snapshot: Dict[str, int] = {
            "system_tokens": system_tokens,
            "available_for_messages": 0,
        }

        available = 0
        if token_budget is not None:
            budget_snapshot["max_tokens"] = token_budget.max_tokens
            budget_snapshot["used_tokens"] = token_budget.used_tokens
            available = token_budget.remaining - system_tokens
            budget_snapshot["available_for_messages"] = max(0, available)

        # Step 4: 消息截断（Tier-3 Token 截断）
        processed_messages: List[Dict[str, str]] = []
        truncated = False
        truncation_desc = ""

        if messages:
            strategy = self._resolve_strategy(strategy_name)
            if available > 0:
                processed_messages = strategy.apply(list(messages), available, self._tokenizer)
                if len(processed_messages) < len(messages):
                    truncated = True
                    truncation_desc = f"{strategy_name}: {len(messages)} -> {len(processed_messages)} msgs"
            else:
                # 没有可用空间，空消息列表
                processed_messages = []
                truncated = True
                truncation_desc = f"no_tokens_available"

        # Step 5: 组装最终文本
        final_lines = [system_text] if system_text else []
        for msg in processed_messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", ""))
            final_lines.append(f"[{role}] {content}")

        final_text = "\n\n".join(final_lines)
        total_tokens = self._count_tokens(final_text)

        # Step 6: 硬上限检查
        if token_budget is not None and self.config.raise_on_exceed:
            if total_tokens > token_budget.max_tokens:
                raise ValueError(f"prompt_exceeds_budget: total={total_tokens} > max={token_budget.max_tokens}")

        return AssembledPrompt(
            text=final_text,
            token_count=total_tokens,
            segments_used=tuple(seg.name for seg in rendered_segments),
            truncated=truncated,
            truncation_strategy=truncation_desc,
            budget_snapshot=budget_snapshot,
        )

    def assemble_simple(
        self,
        system_prompt: str,
        *,
        messages: Optional[List[Dict[str, str]]] = None,
        token_budget: Optional[TokenBudget] = None,
    ) -> AssembledPrompt:
        """便捷方法：直接传入系统提示词字符串，等价于单片段 assemble。"""
        return self.assemble(
            segments=[PromptSegment("inline", system_prompt, priority=50)],
            variables={},
            messages=messages,
            token_budget=token_budget,
        )

    # ---- internal helpers ----

    def _count_tokens(self, text: str) -> int:
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(str(text or "")))
        return max(1, len(str(text or "")) // 4 + 1)

    def _resolve_strategy(self, name: str) -> TruncationStrategy:
        if name == "preserve_milestones":
            return PreserveMilestonesStrategy()
        if name == "head_only":
            return HeadOnlyStrategy()
        return PreserveMilestonesStrategy()

    def cache_stats(self) -> Dict[str, Any]:
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "cache_size": len(self._template_cache),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": round(hit_rate, 4),
        }
