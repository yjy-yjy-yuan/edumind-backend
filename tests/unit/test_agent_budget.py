"""TokenBudget 硬限流单测。"""

import pytest
from app.agents.budget import TokenBudget
from app.agents.exceptions import BudgetExceededError


def test_token_budget_first_charge_raises_when_exceeding_max():
    b = TokenBudget(max_tokens=50)
    with pytest.raises(BudgetExceededError, match="token_budget_exceeded"):
        b.charge("planner_context", 120)


def test_token_budget_charge_raises_when_cumulative_exceeds_max():
    b = TokenBudget(max_tokens=100)
    b.charge("a", 50)
    b.charge("b", 50)
    with pytest.raises(BudgetExceededError):
        b.charge("c", 1)


def test_token_budget_allows_exact_fill():
    b = TokenBudget(max_tokens=100)
    b.charge("a", 100)
    assert b.used_tokens == 100
    assert b.remaining == 0
