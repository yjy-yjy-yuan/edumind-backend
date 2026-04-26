"""智能体编排运行时（Planner / Executor / Validator + 治理网关 + 轨迹记录）。

业务侧效果能力仍落在 ``app/services``；本包提供统一编排入口与不可绕过的工具执行路径。
"""

from app.agents.budget import BudgetExceededError
from app.agents.exceptions import GovernanceError
from app.agents.prompt_engine import (
    PromptEngine,
    PromptEngineConfig,
    PromptSegment,
    TokenBudget,
)
from app.agents.prompts.versions import (
    LEARNING_FLOW_PROMPT_VERSION,
    ORCHESTRATION_PIPELINE_VERSION,
)
from app.agents.skill_registry import (
    SkillRegistry,
    get_skill_registry,
    reset_registry_for_tests,
)
from app.agents.trajectory import (
    AgentTrajectoryRecorder,
    EpisodeRecord,
    StepRecord,
    ToolCall,
    get_trajectory_recorder,
    reset_recorder_for_tests,
)

__all__ = [
    # 编排
    "LEARNING_FLOW_PROMPT_VERSION",
    "ORCHESTRATION_PIPELINE_VERSION",
    # 治理
    "GovernanceError",
    "BudgetExceededError",
    # Prompt Engine（高效）
    "PromptEngine",
    "PromptEngineConfig",
    "PromptSegment",
    "TokenBudget",
    # Skill Registry（可更新）
    "SkillRegistry",
    "get_skill_registry",
    "reset_registry_for_tests",
    # Trajectory（可复利）
    "AgentTrajectoryRecorder",
    "EpisodeRecord",
    "StepRecord",
    "ToolCall",
    "get_trajectory_recorder",
    "reset_recorder_for_tests",
]
