"""任务 Checkpoint 数据库模型（SQLAlchemy 2.0）。

对应 app/tasks/resumable_state_machine.py 中的 DBCheckpointStore。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskCheckpoint(Base):
    """
    任务 Checkpoint 表。

    用于 DBCheckpointStore 实现可断点续传的任务状态持久化。
    """

    __tablename__ = "task_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 任务唯一标识
    task_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)

    # 任务类型（如 video_processing, vector_indexing）
    task_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 当前状态
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 当前阶段
    current_phase: Mapped[str] = mapped_column(String(64), default="")

    # 完整上下文（JSON）
    context_json: Mapped[str] = mapped_column(Text, default="{}")

    # 时间戳
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    __table_args__ = (Index("ix_checkpoints_task_name_state", "task_name", "state"),)
