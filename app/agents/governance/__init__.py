"""工具治理：白名单、参数校验、审计；外部副作用仅允许经 ``execute_tool``。"""

from app.agents.exceptions import GovernanceError
from app.agents.governance.gateway import execute_tool

__all__ = ["execute_tool", "GovernanceError"]
