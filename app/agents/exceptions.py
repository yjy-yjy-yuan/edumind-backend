"""智能体编排与治理层异常。"""


class GovernanceError(RuntimeError):
    """工具未注册、参数非法、预算耗尽或执行被拒绝。"""


class BudgetExceededError(GovernanceError):
    """Token 预算硬限制触发，编排应中断后续步骤。"""
