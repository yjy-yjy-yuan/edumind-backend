"""阶段级断点续传状态机。

为长时间后台任务提供：
  - 多阶段状态机（PENDING → RUNNING → CHECKPOINTED → COMPLETED/FAILED/PAUSED）
  - 阶段级 checkpoint 持久化（每阶段完成后写盘）
  - 进程崩溃后自动恢复（扫描 PENDING/RUNNING 状态的任务）
  - 脏文件系统清理（finally 块 + 临时文件追踪）

使用示例::

    from app.tasks.resumable_state_machine import (
        ResumableTask,
        TaskState,
        get_task_store,
    )

    class MyVideoTask(ResumableTask):
        NAME = "video_processing"
        PHASES = ["download", "transcribe", "index"]

        def run_phase(self, phase: str, state: TaskState) -> None:
            if phase == "download":
                ...download logic...
            elif phase == "transcribe":
                ...transcribe logic...

    task = MyVideoTask(task_id="vid_123")
    task.execute()
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------


class TaskState(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"  # 等待执行
    RUNNING = "running"  # 执行中
    CHECKPOINTED = "checkpointed"  # 阶段完成，已保存 checkpoint
    PAUSED = "paused"  # 暂停（可恢复）
    COMPLETED = "completed"  # 全部完成
    FAILED = "failed"  # 失败（不可恢复）
    CANCELLED = "cancelled"  # 主动取消


class PhaseResult(str, Enum):
    """单阶段执行结果。"""

    SUCCESS = "success"
    RETRY = "retry"  # 可重试错误
    SKIP = "skip"  # 跳过（如已存在）
    FAIL = "fail"  # 不可重试错误


# ---------------------------------------------------------------
# Task State & Checkpoint
# ---------------------------------------------------------------


@dataclass
class PhaseCheckpoint:
    """单个阶段的 checkpoint 数据。"""

    phase: str
    state: Dict[str, Any] = field(default_factory=dict)  # 阶段内部状态
    started_at: str = ""  # ISO8601
    finished_at: str = ""  # ISO8601
    latency_ms: float = 0.0
    result: str = PhaseResult.SUCCESS.value
    error: str = ""


@dataclass
class TaskContext:
    """
    任务上下文（跨阶段共享）。

    在 execute() 入口创建，随 checkpoint 持久化。
    """

    task_id: str
    task_name: str
    state: str = TaskState.PENDING.value
    current_phase: str = ""
    phase_index: int = 0
    checkpoints: Dict[str, PhaseCheckpoint] = field(default_factory=dict)
    # 跨阶段共享数据（如下载路径、视频 ID 等）
    shared_data: Dict[str, Any] = field(default_factory=dict)
    # 临时文件追踪（用于 finally 清理）
    temp_files: List[str] = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_latency_ms: float = 0.0
    # 重试计数
    retry_count: int = 0
    max_retries: int = 3
    trace_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "state": self.state,
            "current_phase": self.current_phase,
            "phase_index": self.phase_index,
            "checkpoints": {k: asdict(v) for k, v in self.checkpoints.items()},
            "shared_data": dict(self.shared_data),
            "temp_files": list(self.temp_files),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_latency_ms": self.total_latency_ms,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskContext":
        checkpoints = {}
        for k, v in data.get("checkpoints", {}).items():
            checkpoints[k] = PhaseCheckpoint(**v) if isinstance(v, dict) else v
        return cls(
            task_id=str(data["task_id"]),
            task_name=str(data["task_name"]),
            state=str(data.get("state", TaskState.PENDING.value)),
            current_phase=str(data.get("current_phase", "")),
            phase_index=int(data.get("phase_index", 0)),
            checkpoints=checkpoints,
            shared_data=dict(data.get("shared_data", {})),
            temp_files=list(data.get("temp_files", [])),
            error=str(data.get("error", "")),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            total_latency_ms=float(data.get("total_latency_ms", 0.0)),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            trace_id=str(data.get("trace_id", "")),
        )


# ---------------------------------------------------------------
# Checkpoint Store（后端抽象）
# ---------------------------------------------------------------


class CheckpointStoreBackend(ABC):
    """Checkpoint 存储后端抽象。"""

    @abstractmethod
    def save(self, ctx: TaskContext) -> None:
        raise NotImplementedError

    @abstractmethod
    def load(self, task_id: str) -> Optional[TaskContext]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, task_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_pending(self, task_name: str) -> List[TaskContext]:
        raise NotImplementedError


class FileCheckpointStore(CheckpointStoreBackend):
    """
    文件系统 checkpoint 存储（开发/测试阶段）。

    文件路径：{data_dir}/{task_name}/{task_id}.json
    """

    def __init__(self, data_dir: Optional[str | Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).resolve().parents[2] / "data" / "task_checkpoints"
        if isinstance(data_dir, str):
            data_dir = Path(data_dir)
        self._data_dir: Path = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _task_dir(self, task_name: str) -> Path:
        d = self._data_dir / task_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _task_path(self, task_name: str, task_id: str) -> Path:
        safe = task_id.replace("/", "_").replace("\\", "_")
        return self._task_dir(task_name) / f"{safe}.json"

    def save(self, ctx: TaskContext) -> None:
        path = self._task_path(ctx.task_name, ctx.task_id)
        with self._lock:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(ctx.to_dict(), ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(path)

    def load(self, task_id: str) -> Optional[TaskContext]:
        for store_dir in self._data_dir.iterdir():
            if not store_dir.is_dir():
                continue
            path = self._task_path(store_dir.name, task_id)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return TaskContext.from_dict(data)
                except Exception:
                    logger.warning("failed to load checkpoint %s", path, exc_info=True)
        return None

    def delete(self, task_id: str) -> None:
        for store_dir in self._data_dir.iterdir():
            if not store_dir.is_dir():
                continue
            path = self._task_path(store_dir.name, task_id)
            if path.exists():
                path.unlink()
                logger.debug("deleted checkpoint %s", path)

    def list_pending(self, task_name: str) -> List[TaskContext]:
        results = []
        d = self._task_dir(task_name)
        if not d.exists():
            return results
        for path in d.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ctx = TaskContext.from_dict(data)
                if ctx.state in (TaskState.PENDING.value, TaskState.RUNNING.value, TaskState.CHECKPOINTED.value):
                    results.append(ctx)
            except Exception:
                continue
        return results


class DBCheckpointStore(CheckpointStoreBackend):
    """
    数据库 checkpoint 存储（生产环境）。

    依赖 app/models/task_checkpoint.py 中的 TaskCheckpoint 模型。
    """

    def __init__(self, db_session_factory=None):
        self._session_factory = db_session_factory

    def _session(self):
        if self._session_factory is None:
            from app.core.database import SessionLocal

            return SessionLocal()
        return self._session_factory()

    def save(self, ctx: TaskContext) -> None:
        from app.models.task_checkpoint import TaskCheckpoint

        db = self._session()
        try:
            row = db.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == ctx.task_id).first()
            data = ctx.to_dict()
            if row is None:
                row = TaskCheckpoint(
                    task_id=ctx.task_id,
                    task_name=ctx.task_name,
                    state=ctx.state,
                    current_phase=ctx.current_phase,
                    context_json=json.dumps(data, ensure_ascii=False, default=str),
                    updated_at=_utc_now_iso(),
                )
                db.add(row)
            else:
                row.state = ctx.state
                row.current_phase = ctx.current_phase
                row.context_json = json.dumps(data, ensure_ascii=False, default=str)
                row.updated_at = _utc_now_iso()
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def load(self, task_id: str) -> Optional[TaskContext]:
        from app.models.task_checkpoint import TaskCheckpoint

        db = self._session()
        try:
            row = db.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == task_id).first()
            if row is None:
                return None
            return TaskContext.from_dict(json.loads(row.context_json or "{}"))
        finally:
            db.close()

    def delete(self, task_id: str) -> None:
        from app.models.task_checkpoint import TaskCheckpoint

        db = self._session()
        try:
            db.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == task_id).delete()
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_pending(self, task_name: str) -> List[TaskContext]:
        from app.models.task_checkpoint import TaskCheckpoint

        db = self._session()
        try:
            rows = (
                db.query(TaskCheckpoint)
                .filter(
                    TaskCheckpoint.task_name == task_name,
                    TaskCheckpoint.state.in_(
                        [
                            TaskState.PENDING.value,
                            TaskState.RUNNING.value,
                            TaskState.CHECKPOINTED.value,
                        ]
                    ),
                )
                .all()
            )
            results = []
            for row in rows:
                try:
                    ctx = TaskContext.from_dict(json.loads(row.context_json or "{}"))
                    results.append(ctx)
                except Exception:
                    continue
            return results
        finally:
            db.close()


# ---------------------------------------------------------------
# Global Store
# ---------------------------------------------------------------

_task_store: Optional[CheckpointStoreBackend] = None
_task_store_lock = threading.Lock()


def get_task_store() -> CheckpointStoreBackend:
    global _task_store
    if _task_store is None:
        with _task_store_lock:
            if _task_store is None:
                _task_store = FileCheckpointStore()
    return _task_store


def reset_task_store_for_tests(store: Optional[CheckpointStoreBackend] = None) -> None:
    global _task_store
    with _task_store_lock:
        _task_store = store


# ---------------------------------------------------------------
# Resumable Task Base
# ---------------------------------------------------------------


class ResumableTask(ABC):
    """
    可断点续传任务基类。

    子类必须定义：
      - NAME: str —— 任务类型标识
      - PHASES: List[str] —— 阶段列表（顺序执行）
      - run_phase(phase, ctx) -> PhaseResult

    自动处理：
      - 状态机转换
      - Checkpoint 持久化（每阶段完成后）
      - 崩溃恢复（execute 时检测已有 checkpoint）
      - 临时文件清理（finally 块）
    """

    NAME: str = "resumable"
    PHASES: List[str] = []

    def __init__(
        self,
        task_id: Optional[str] = None,
        store: Optional[CheckpointStoreBackend] = None,
        trace_id: Optional[str] = None,
    ):
        self.task_id = task_id or f"{self.NAME}_{uuid.uuid4().hex[:12]}"
        self._store = store or get_task_store()
        self._ctx: Optional[TaskContext] = None
        self._trace_id = trace_id or ""

    # ---- abstract interface ----

    @abstractmethod
    def run_phase(self, phase: str, ctx: TaskContext) -> PhaseResult:
        """
        执行单个阶段。

        ctx.shared_data: 跨阶段共享数据，可读写
        ctx.checkpoints[phase]: 阶段级 checkpoint 数据

        Returns:
            PhaseResult.SUCCESS   — 成功，进入下一阶段
            PhaseResult.RETRY    — 可重试错误，重试当前阶段
            PhaseResult.SKIP     — 跳过（如已存在），进入下一阶段
            PhaseResult.FAIL     — 不可重试错误，任务失败
        """
        raise NotImplementedError

    def on_task_start(self, ctx: TaskContext) -> None:
        """任务开始前的回调（可override）。"""

    def on_task_complete(self, ctx: TaskContext) -> None:
        """任务完成后的回调（可override）。"""

    def on_task_fail(self, ctx: TaskContext) -> None:
        """任务失败后的回调（可override）。"""

    def cleanup_temp_files(self, ctx: TaskContext) -> None:
        """
        清理临时文件。

        默认实现：删除 ctx.temp_files 中的路径。

        子类可 override 添加自定义清理逻辑（如 ffmpeg 中间文件）。
        """
        for path_str in list(ctx.temp_files):
            try:
                p = Path(path_str)
                if p.exists() and p.is_file():
                    p.unlink()
                    logger.debug("cleaned temp file %s", path_str)
            except Exception as exc:
                logger.debug("failed to clean temp file %s: %s", path_str, exc)

    # ---- execution engine ----

    def execute(self) -> TaskContext:
        """
        执行任务（支持断点续传）。

        执行逻辑：
          1. 尝试加载已有 checkpoint
          2. 恢复模式：从断点继续执行
          3. 新任务：从 PENDING 开始执行
          4. 每阶段完成后写 checkpoint
          5. finally 块执行临时文件清理
        """
        ctx = self._store.load(self.task_id)

        if ctx is not None:
            logger.info(
                "resuming task %s | state=%s | current_phase=%s | retry_count=%d | trace_id=%s",
                self.task_id,
                ctx.state,
                ctx.current_phase,
                ctx.retry_count,
                ctx.trace_id,
            )
        else:
            ctx = TaskContext(
                task_id=self.task_id,
                task_name=self.NAME,
                state=TaskState.PENDING.value,
                trace_id=self._trace_id,
                started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            logger.info("starting new task %s | trace_id=%s", self.task_id, self._trace_id)

        self._ctx = ctx
        started_at = time.monotonic()

        try:
            self.on_task_start(ctx)
            self._execute_impl(ctx)
        except Exception as exc:
            ctx.state = TaskState.FAILED.value
            ctx.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            logger.exception("task %s failed | trace_id=%s", self.task_id, ctx.trace_id)
            self.on_task_fail(ctx)
        finally:
            ctx.total_latency_ms = (time.monotonic() - started_at) * 1000
            ctx.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            # 临时文件清理（即使失败也要清理）
            try:
                self.cleanup_temp_files(ctx)
            except Exception:
                pass
            # 保存最终状态
            self._store.save(ctx)

        return ctx

    def _execute_impl(self, ctx: TaskContext) -> None:
        """内部执行逻辑（状态机驱动）。"""
        phases = self.PHASES
        if not phases:
            ctx.state = TaskState.COMPLETED.value
            self.on_task_complete(ctx)
            return

        # 恢复判断：已完成/已失败的任务直接跳过
        if ctx.state in (TaskState.COMPLETED.value, TaskState.FAILED.value, TaskState.CANCELLED.value):
            logger.debug("task %s already %s, skipping execution", self.task_id, ctx.state)
            return

        # 断点续传：从 checkpoint 阶段继续
        start_index = 0
        if ctx.current_phase and ctx.current_phase in phases:
            if ctx.state == TaskState.RUNNING.value:
                # 进程崩溃于上一阶段，需重新执行该阶段
                start_index = max(0, phases.index(ctx.current_phase))
            elif ctx.state == TaskState.CHECKPOINTED.value:
                # 上一阶段已成功 checkpoint，跳过该阶段
                last_phase_index = phases.index(ctx.current_phase)
                # 如果最后 checkpoint 已是最后阶段，直接完成
                if last_phase_index >= len(phases) - 1:
                    ctx.state = TaskState.COMPLETED.value
                    self.on_task_complete(ctx)
                    return
                # 否则从下一阶段开始
                start_index = last_phase_index + 1

        for i, phase in enumerate(phases):
            if i < start_index:
                continue

            ctx.current_phase = phase
            ctx.phase_index = i
            ctx.state = TaskState.RUNNING.value
            self._store.save(ctx)

            phase_started = time.monotonic()
            checkpoint = ctx.checkpoints.get(phase) or PhaseCheckpoint(phase=phase)
            checkpoint.started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            try:
                result = self.run_phase(phase, ctx)
            except Exception as exc:
                logger.warning("phase %s exception | task=%s | error=%s", phase, self.task_id, exc)
                result = PhaseResult.FAIL

            checkpoint.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            checkpoint.latency_ms = (time.monotonic() - phase_started) * 1000
            checkpoint.result = result.value

            if result == PhaseResult.SUCCESS:
                ctx.checkpoints[phase] = checkpoint
                ctx.state = TaskState.CHECKPOINTED.value
                self._store.save(ctx)
                logger.debug(
                    "phase %s completed | task=%s | latency_ms=%.1f",
                    phase,
                    self.task_id,
                    checkpoint.latency_ms,
                )

            elif result == PhaseResult.SKIP:
                checkpoint.result = PhaseResult.SKIP.value
                ctx.checkpoints[phase] = checkpoint
                ctx.state = TaskState.CHECKPOINTED.value
                self._store.save(ctx)
                logger.debug("phase %s skipped | task=%s", phase, self.task_id)

            elif result == PhaseResult.RETRY:
                ctx.retry_count += 1
                if ctx.retry_count > ctx.max_retries:
                    checkpoint.error = f"max retries exceeded ({ctx.retry_count})"
                    ctx.error = checkpoint.error
                    ctx.state = TaskState.FAILED.value
                    self._store.save(ctx)
                    self.on_task_fail(ctx)
                    return
                ctx.state = TaskState.PENDING.value
                self._store.save(ctx)
                logger.warning(
                    "phase %s retry | task=%s | retry=%d/%d",
                    phase,
                    self.task_id,
                    ctx.retry_count,
                    ctx.max_retries,
                )
                return self._execute_impl(ctx)  # 重试当前任务

            else:  # FAIL
                ctx.state = TaskState.FAILED.value
                ctx.error = checkpoint.error or f"phase {phase} failed"
                self._store.save(ctx)
                self.on_task_fail(ctx)
                return

        # 所有阶段完成
        ctx.state = TaskState.COMPLETED.value
        self._store.save(ctx)
        self.on_task_complete(ctx)

    def cancel(self) -> None:
        """主动取消任务。"""
        ctx = self._store.load(self.task_id)
        if ctx is None:
            return
        ctx.state = TaskState.CANCELLED.value
        ctx.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._store.save(ctx)
        # 清理临时文件
        self.cleanup_temp_files(ctx)

    def add_temp_file(self, path: str) -> None:
        """追踪临时文件路径（用于 finally 清理）。"""
        if self._ctx is not None:
            if path not in self._ctx.temp_files:
                self._ctx.temp_files.append(path)
