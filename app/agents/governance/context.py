"""Governance execution context guards.

Tool implementations must only run inside this context, so direct invocation
outside the governance gateway can be blocked deterministically.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.agents.exceptions import GovernanceError

_IN_GOVERNANCE_GATEWAY: ContextVar[bool] = ContextVar("_IN_GOVERNANCE_GATEWAY", default=False)


@contextmanager
def governance_execution_context() -> Iterator[None]:
    """Mark current execution as running under governance gateway."""
    token = _IN_GOVERNANCE_GATEWAY.set(True)
    try:
        yield
    finally:
        _IN_GOVERNANCE_GATEWAY.reset(token)


def ensure_in_governance_context() -> None:
    """Reject direct tool calls that bypass gateway entrypoint."""
    if not _IN_GOVERNANCE_GATEWAY.get():
        raise GovernanceError("governance_bypass_blocked")
