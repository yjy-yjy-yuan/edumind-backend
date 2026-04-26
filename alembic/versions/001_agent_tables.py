"""Add agent_trajectory and task_checkpoint tables.

Revision ID: 001_agent_tables
Revises:
Create Date: 2026-04-23

- agent_trajectory_episodes: Agent 轨迹会话表（planner/executor/validator 每轮一个 episode）
- agent_trajectory_steps:     Agent 轨迹步骤表（每个 phase 对应一条 step）
- task_checkpoints:           任务 Checkpoint 表（ResumableTask 断点续传）

回填步骤（生产环境）：
  1. python scripts/init_db.py --create   # 初始化新表
  2. 验证 trajectory 数据已写入（SELECT COUNT(*) FROM agent_trajectory_episodes）
  3. 验证 checkpoint 数据已写入（SELECT COUNT(*) FROM task_checkpoints）
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic revision.
revision: str = "001_agent_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # agent_trajectory_episodes
    # ---------------------------------------------------------------
    op.create_table(
        "agent_trajectory_episodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("episode_id", sa.String(64), nullable=False),
        sa.Column("pipeline", sa.String(64), nullable=False),
        sa.Column("pipeline_version", sa.String(32), server_default="", nullable=False),
        sa.Column("skill_version", sa.String(32), server_default="", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("final_result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.Column("total_latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("trace_id", sa.String(128), server_default="", nullable=False),
        sa.Column("user_id_hash", sa.String(64), server_default="", nullable=False),
        sa.Column("video_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_episodes_episode_id", "agent_trajectory_episodes", ["episode_id"], unique=True)
    op.create_index("ix_episodes_pipeline", "agent_trajectory_episodes", ["pipeline"])
    op.create_index("ix_episodes_started_pipeline", "agent_trajectory_episodes", ["started_at", "pipeline"])
    op.create_index("ix_episodes_status_pipeline", "agent_trajectory_episodes", ["status", "pipeline"])
    op.create_index("ix_episodes_trace_id", "agent_trajectory_episodes", ["trace_id"])

    # ---------------------------------------------------------------
    # agent_trajectory_steps
    # ---------------------------------------------------------------
    op.create_table(
        "agent_trajectory_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("episode_id", sa.String(64), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("action_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("agent_state_before_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("agent_state_after_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("llm_raw_output", sa.Text(), nullable=True),
        sa.Column("tool_calls_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("validation_result_json", sa.Text(), nullable=True),
        sa.Column("governance_decision_json", sa.Text(), nullable=True),
        sa.Column("human_feedback_json", sa.Text(), nullable=True),
        sa.Column("started_at", sa.String(32), nullable=True),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_steps_episode_id", "agent_trajectory_steps", ["episode_id"])
    op.create_index("ix_steps_phase", "agent_trajectory_steps", ["phase"])
    op.create_index("ix_steps_episode_index", "agent_trajectory_steps", ["episode_id", "step_index"])

    # ---------------------------------------------------------------
    # task_checkpoints
    # ---------------------------------------------------------------
    op.create_table(
        "task_checkpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("task_name", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_phase", sa.String(64), server_default="", nullable=False),
        sa.Column("context_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.String(32), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checkpoints_task_id", "task_checkpoints", ["task_id"], unique=True)
    op.create_index("ix_checkpoints_task_name", "task_checkpoints", ["task_name"])
    op.create_index("ix_checkpoints_task_name_state", "task_checkpoints", ["task_name", "state"])


def downgrade() -> None:
    op.drop_table("task_checkpoints")
    op.drop_table("agent_trajectory_steps")
    op.drop_table("agent_trajectory_episodes")
