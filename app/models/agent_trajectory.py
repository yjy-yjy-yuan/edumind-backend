"""Agent Trajectory 数据库模型（SQLAlchemy 2.0）。

对应 app/agents/trajectory.py 中的 DBBackend。

状态机：
  TrajectoryEpisode.status → running | completed | failed | cancelled
  TrajectoryStep.phase   → planner | executor | validator | governance | unknown

使用 Alembic 管理迁移；当前使用 Base.metadata.create_all() 自动建表（开发阶段）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TrajectoryEpisode(Base):
    """
    Agent 轨迹会话表（对应一次完整的 pipeline 执行）。

    与 TrajectoryStep 为一对多关系（按 episode_id 关联）。
    """

    __tablename__ = "agent_trajectory_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 全局唯一会话标识（UUID，由业务层生成）
    episode_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Pipeline 元信息
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pipeline_version: Mapped[str] = mapped_column(String(32), default="")
    skill_version: Mapped[str] = mapped_column(String(32), default="")

    # 执行状态
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # running|completed|failed|cancelled

    # 结构化字段（JSON 存储）
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    final_result_json: Mapped[str] = mapped_column(Text, nullable=True)

    # 错误信息
    error: Mapped[str] = mapped_column(Text, nullable=True)

    # 时间戳
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str] = mapped_column(String(32), nullable=True)

    # 性能指标
    total_latency_ms: Mapped[float] = mapped_column(BigInteger, nullable=True)

    # 数据血缘字段（脱敏）
    trace_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    user_id_hash: Mapped[str] = mapped_column(String(64), default="")  # SHA-256 哈希
    video_id: Mapped[int] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        Index("ix_episodes_started_pipeline", "started_at", "pipeline"),
        Index("ix_episodes_status_pipeline", "status", "pipeline"),
    )


class TrajectoryStep(Base):
    """
    Agent 轨迹步骤表（对应 pipeline 中每个 phase）。

    记录：
    - 执行的动作和参数
    - 治理决策
    - 验证结果
    - 工具调用链（含耗时）
    - LLM 原始输出（用于复盘和训练数据构建）
    """

    __tablename__ = "agent_trajectory_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键关联（逻辑外键，不设 DB 级约束以避免级联删除问题）
    episode_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 步骤序号
    step_index: Mapped[int] = mapped_column(nullable=False)

    # 阶段
    phase: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # planner|executor|validator|governance|unknown

    # 动作
    action_json: Mapped[str] = mapped_column(Text, default="{}")

    # 状态快照
    agent_state_before_json: Mapped[str] = mapped_column(Text, default="{}")
    agent_state_after_json: Mapped[str] = mapped_column(Text, default="{}")

    # LLM 原始输出（可用于训练数据构建）
    llm_raw_output: Mapped[str] = mapped_column(Text, nullable=True)

    # 工具调用链
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")

    # 验证与治理
    validation_result_json: Mapped[str] = mapped_column(Text, nullable=True)
    governance_decision_json: Mapped[str] = mapped_column(Text, nullable=True)
    human_feedback_json: Mapped[str] = mapped_column(Text, nullable=True)

    # 时间戳与耗时
    started_at: Mapped[str] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[float] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (Index("ix_steps_episode_index", "episode_id", "step_index"),)
