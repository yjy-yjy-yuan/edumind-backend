"""Token / 成本预算（按步扣减，可观测剩余额度）。"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from app.agents.exceptions import BudgetExceededError

__all__ = ["TokenBudget", "BudgetExceededError"]


@dataclass
class TokenBudget:
    """粗粒度 token 预算：按步累计估算，用于编排层限流与遥测。"""

    max_tokens: int
    used_tokens: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)

    def charge(self, step: str, estimated_tokens: int) -> None:
        est = max(0, int(estimated_tokens))
        if self.used_tokens + est > self.max_tokens:
            raise BudgetExceededError(
                f"token_budget_exceeded|step={step}|max={self.max_tokens}|would_use={self.used_tokens + est}"
            )
        self.used_tokens += est
        self.steps.append({"step": step, "estimated_tokens": est})

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "used_tokens_estimated": self.used_tokens,
            "remaining_estimated": self.remaining,
            "steps": list(self.steps),
        }
