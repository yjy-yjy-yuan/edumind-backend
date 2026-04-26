"""Agent Trajectory 记录器——系统级（OS）持久化，不依赖单个脚本。

记录内容（每一步）：
  1. episode_id       — 唯一会话/任务标识
  2. step_index       — 步骤序号（0=第一步）
  3. phase            — 阶段：planner | executor | validator | governance
  4. agent_state_before  — 执行前状态快照（JSON）
  5. action           — 采取的动作（plan / execute / validate + 参数）
  6. llm_raw_output   — 模型原始输出（不含解析后的结构）
  7. tool_calls       — 工具调用列表（含参数、结果、耗时）
  8. agent_state_after — 执行后状态快照
  9. validation_result — 验证结果
  10. governance_decision — 治理决策
  11. human_feedback  — 人工反馈（如果有）

存储策略：
  - 热数据（近 7 天）：SQLAlchemy ORM（app/models/agent_trajectory.py）
  - 温数据（7~90 天）：Parquet 文件（按 episode_id 分区）
  - 冷数据（> 90 天）：OSS/S3（归档，由导出任务处理）

使用示例::

    from app.agents.trajectory import get_trajectory_recorder

    recorder = get_trajectory_recorder()
    episode = recorder.start_episode(
        episode_id="ep_abc123",
        pipeline="learning_flow",
        metadata={"video_id": 42, "user_id": 7},
    )
    recorder.record_step(
        episode,
        phase="executor",
        action={"tool": "lf_vinci_chat", "params": {...}},
        tool_calls=[{"name": "lf_vinci_chat", "duration_ms": 230}],
    )
    recorder.finish_episode(episode, final_result={...})
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------


class TrajectoryPhase(str, Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VALIDATOR = "validator"
    GOVERNANCE = "governance"
    UNKNOWN = "unknown"


class TrajectoryStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ToolCall:
    """单次工具调用记录。"""

    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    governed: bool = True  # 是否经过治理网关

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "params": dict(self.params),
            "result": dict(self.result) if self.result else None,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "governed": self.governed,
        }


@dataclass
class StepRecord:
    """单步轨迹记录。"""

    step_index: int
    phase: str  # TrajectoryPhase value
    action: Dict[str, Any]
    agent_state_before: Dict[str, Any] = field(default_factory=dict)
    agent_state_after: Dict[str, Any] = field(default_factory=dict)
    llm_raw_output: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    validation_result: Optional[Dict[str, Any]] = None
    governance_decision: Optional[Dict[str, Any]] = None
    human_feedback: Optional[Dict[str, Any]] = None
    started_at: str = ""  # ISO8601
    finished_at: str = ""  # ISO8601
    latency_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "phase": self.phase,
            "action": dict(self.action),
            "agent_state_before": dict(self.agent_state_before),
            "agent_state_after": dict(self.agent_state_after),
            "llm_raw_output": self.llm_raw_output,
            "tool_calls": [dict(tc) if isinstance(tc, dict) else tc for tc in self.tool_calls],
            "validation_result": dict(self.validation_result) if self.validation_result else None,
            "governance_decision": dict(self.governance_decision) if self.governance_decision else None,
            "human_feedback": dict(self.human_feedback) if self.human_feedback else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latency_ms": self.latency_ms,
        }


@dataclass
class EpisodeRecord:
    """一次完整 agent 会话的轨迹。"""

    episode_id: str
    pipeline: str
    pipeline_version: str = ""
    skill_version: str = ""
    status: str = TrajectoryStatus.RUNNING.value
    metadata: Dict[str, Any] = field(default_factory=dict)
    steps: List[StepRecord] = field(default_factory=list)
    final_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""
    total_latency_ms: Optional[float] = None
    # OS 级数据血缘字段
    trace_id: str = ""
    user_id_hash: str = ""  # 脱敏后的用户 ID
    video_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "pipeline": self.pipeline,
            "pipeline_version": self.pipeline_version,
            "skill_version": self.skill_version,
            "status": self.status,
            "metadata": dict(self.metadata),
            "steps": [s.to_dict() if isinstance(s, StepRecord) else s for s in self.steps],
            "final_result": dict(self.final_result) if self.final_result else None,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_latency_ms": self.total_latency_ms,
            "trace_id": self.trace_id,
            "user_id_hash": self.user_id_hash,
            "video_id": self.video_id,
        }

    def to_jsonl_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ---------------------------------------------------------------
# Recorder Interface
# ---------------------------------------------------------------


class TrajectoryRecorderBackend(ABC):
    """轨迹存储后端抽象（支持热/温/冷分层存储切换）。"""

    @abstractmethod
    def save_episode(self, episode: EpisodeRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def append_step(self, episode_id: str, step: StepRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def query_episodes(
        self,
        *,
        since: Optional[datetime] = None,
        pipeline: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


# ---------------------------------------------------------------
# JSON Lines Backend（开发/测试用，线程安全）
# ---------------------------------------------------------------


class JsonLinesBackend(TrajectoryRecorderBackend):
    """
    JSONL 文件后端（开发/测试阶段）。

    文件按 episode_id 命名，便于 grep 查询。
    生产环境应替换为 Parquet + S3 后端。
    """

    def __init__(self, output_dir: Optional[str | Path] = None):
        if output_dir is None:
            output_dir = Path(__file__).resolve().parents[2] / "data" / "trajectories"
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
        self._output_dir: Path = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._write_buffer: Dict[str, List[str]] = {}
        self._buffer_size = 50  # 每 N 条写入一次文件

    def _episode_path(self, episode_id: str) -> Path:
        safe = episode_id.replace("/", "_").replace("\\", "_")
        return self._output_dir / f"{safe}.jsonl"

    def save_episode(self, episode: EpisodeRecord) -> None:
        line = episode.to_jsonl_line()
        with self._lock:
            ep_id = episode.episode_id
            if ep_id not in self._write_buffer:
                self._write_buffer[ep_id] = []
            self._write_buffer[ep_id].append(line)
            if len(self._write_buffer[ep_id]) >= self._buffer_size:
                self._flush_episode(ep_id)

    def _flush_episode(self, episode_id: str) -> None:
        lines = self._write_buffer.pop(episode_id, [])
        if not lines:
            return
        path = self._episode_path(episode_id)
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        logger.debug("trajectory flushed %d lines to %s", len(lines), path)

    def append_step(self, episode_id: str, step: StepRecord) -> None:
        step_line = json.dumps({"type": "step", **step.to_dict()}, ensure_ascii=False, default=str)
        with self._lock:
            if episode_id not in self._write_buffer:
                self._write_buffer[episode_id] = []
            self._write_buffer[episode_id].append(step_line)
            if len(self._write_buffer[episode_id]) >= self._buffer_size:
                self._flush_episode(episode_id)

    def flush_all(self) -> None:
        """强制写出所有缓冲数据（服务关闭时调用）。"""
        with self._lock:
            episode_ids = list(self._write_buffer.keys())
        for ep_id in episode_ids:
            self._flush_episode(ep_id)
        logger.info("trajectory flushed all episodes (%d)", len(episode_ids))

    def query_episodes(
        self,
        *,
        since: Optional[datetime] = None,
        pipeline: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        pattern = f"*.jsonl" if pipeline is None else f"*{pipeline}*.jsonl"
        for path in self._output_dir.glob(pattern):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            if record.get("type") == "step":
                                continue  # 跳过中间步骤行
                            if since:
                                started = record.get("started_at", "")
                                if started and datetime.fromisoformat(started.replace("Z", "+00:00")) < since:
                                    continue
                            if pipeline and record.get("pipeline") != pipeline:
                                continue
                            if status and record.get("status") != status:
                                continue
                            results.append(record)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue
            if len(results) >= limit:
                break
        return results[:limit]


# ---------------------------------------------------------------
# DB Backend（生产热数据，SQLAlchemy）
# ---------------------------------------------------------------


class DBBackend(TrajectoryRecorderBackend):
    """
    SQLAlchemy ORM 后端（生产热数据）。

    依赖 app/models/agent_trajectory.py 中的 TrajectoryEpisode 和 TrajectoryStep 模型。
    """

    def __init__(self, db_session_factory=None):
        self._session_factory = db_session_factory
        self._lock = threading.Lock()

    def _session(self):
        if self._session_factory is None:
            from app.core.database import SessionLocal

            return SessionLocal()
        return self._session_factory()

    def save_episode(self, episode: EpisodeRecord) -> None:
        from app.models.agent_trajectory import TrajectoryEpisode, TrajectoryStep

        db = self._session()
        try:
            ep = TrajectoryEpisode(
                episode_id=episode.episode_id,
                pipeline=episode.pipeline,
                pipeline_version=episode.pipeline_version,
                skill_version=episode.skill_version,
                status=episode.status,
                metadata_json=json.dumps(episode.metadata, ensure_ascii=False, default=str),
                final_result_json=(
                    json.dumps(episode.final_result, ensure_ascii=False, default=str) if episode.final_result else None
                ),
                error=episode.error,
                started_at=episode.started_at,
                finished_at=episode.finished_at,
                total_latency_ms=episode.total_latency_ms,
                trace_id=episode.trace_id,
                user_id_hash=episode.user_id_hash,
                video_id=episode.video_id,
            )
            db.add(ep)

            for step in episode.steps:
                if isinstance(step, StepRecord):
                    step_data = step.to_dict()
                else:
                    step_data = step
                s = TrajectoryStep(
                    episode_id=episode.episode_id,
                    step_index=step_data.get("step_index", 0),
                    phase=step_data.get("phase", "unknown"),
                    action_json=json.dumps(step_data.get("action", {}), ensure_ascii=False, default=str),
                    agent_state_before_json=json.dumps(
                        step_data.get("agent_state_before", {}), ensure_ascii=False, default=str
                    ),
                    agent_state_after_json=json.dumps(
                        step_data.get("agent_state_after", {}), ensure_ascii=False, default=str
                    ),
                    llm_raw_output=step_data.get("llm_raw_output"),
                    tool_calls_json=json.dumps(step_data.get("tool_calls", []), ensure_ascii=False, default=str),
                    validation_result_json=(
                        json.dumps(step_data.get("validation_result"), ensure_ascii=False, default=str)
                        if step_data.get("validation_result")
                        else None
                    ),
                    governance_decision_json=(
                        json.dumps(step_data.get("governance_decision"), ensure_ascii=False, default=str)
                        if step_data.get("governance_decision")
                        else None
                    ),
                    human_feedback_json=(
                        json.dumps(step_data.get("human_feedback"), ensure_ascii=False, default=str)
                        if step_data.get("human_feedback")
                        else None
                    ),
                    started_at=step_data.get("started_at"),
                    finished_at=step_data.get("finished_at"),
                    latency_ms=step_data.get("latency_ms"),
                )
                db.add(s)

            db.commit()
            logger.debug("trajectory episode %s saved to DB", episode.episode_id)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def append_step(self, episode_id: str, step: StepRecord) -> None:
        from app.models.agent_trajectory import TrajectoryStep

        db = self._session()
        try:
            step_data = step.to_dict() if isinstance(step, StepRecord) else step
            s = TrajectoryStep(
                episode_id=episode_id,
                step_index=step_data.get("step_index", 0),
                phase=step_data.get("phase", "unknown"),
                action_json=json.dumps(step_data.get("action", {}), ensure_ascii=False, default=str),
                agent_state_before_json=json.dumps(
                    step_data.get("agent_state_before", {}), ensure_ascii=False, default=str
                ),
                agent_state_after_json=json.dumps(
                    step_data.get("agent_state_after", {}), ensure_ascii=False, default=str
                ),
                llm_raw_output=step_data.get("llm_raw_output"),
                tool_calls_json=json.dumps(step_data.get("tool_calls", []), ensure_ascii=False, default=str),
                validation_result_json=(
                    json.dumps(step_data.get("validation_result"), ensure_ascii=False, default=str)
                    if step_data.get("validation_result")
                    else None
                ),
                governance_decision_json=(
                    json.dumps(step_data.get("governance_decision"), ensure_ascii=False, default=str)
                    if step_data.get("governance_decision")
                    else None
                ),
                human_feedback_json=(
                    json.dumps(step_data.get("human_feedback"), ensure_ascii=False, default=str)
                    if step_data.get("human_feedback")
                    else None
                ),
                started_at=step_data.get("started_at"),
                finished_at=step_data.get("finished_at"),
                latency_ms=step_data.get("latency_ms"),
            )
            db.add(s)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def query_episodes(
        self,
        *,
        since: Optional[datetime] = None,
        pipeline: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        from app.models.agent_trajectory import TrajectoryEpisode

        db = self._session()
        try:
            q = db.query(TrajectoryEpisode)
            if since:
                q = q.filter(TrajectoryEpisode.started_at >= since.isoformat())
            if pipeline:
                q = q.filter(TrajectoryEpisode.pipeline == pipeline)
            if status:
                q = q.filter(TrajectoryEpisode.status == status)
            rows = q.order_by(TrajectoryEpisode.started_at.desc()).limit(limit).all()
            results = []
            for row in rows:
                results.append(
                    {
                        "episode_id": row.episode_id,
                        "pipeline": row.pipeline,
                        "pipeline_version": row.pipeline_version,
                        "skill_version": row.skill_version,
                        "status": row.status,
                        "metadata": json.loads(row.metadata_json or "{}"),
                        "final_result": json.loads(row.final_result_json or "null"),
                        "error": row.error,
                        "started_at": row.started_at,
                        "finished_at": row.finished_at,
                        "total_latency_ms": row.total_latency_ms,
                        "trace_id": row.trace_id,
                        "user_id_hash": row.user_id_hash,
                        "video_id": row.video_id,
                    }
                )
            return results
        finally:
            db.close()


# ---------------------------------------------------------------
# Agent Trajectory Recorder（主接口）
# ---------------------------------------------------------------


class AgentTrajectoryRecorder:
    """
    Agent 轨迹记录器（对外主接口）。

    支持双后端：
    - JsonLinesBackend（开发/测试）
    - DBBackend（生产热数据）

    典型使用方式::

        recorder = AgentTrajectoryRecorder(backend=JsonLinesBackend())
        episode = recorder.start_episode(
            episode_id=str(uuid.uuid4()),
            pipeline="learning_flow",
            trace_id="trace_abc",
        )
        recorder.record_step(
            episode,
            phase="planner",
            action={"intent": "timestamp_note"},
        )
        recorder.record_step(
            episode,
            phase="executor",
            action={"tool": "lf_vinci_chat", "params": {...}},
            tool_calls=[ToolCall(name="lf_vinci_chat", duration_ms=150).to_dict()],
        )
        recorder.finish_episode(episode, final_result={"note_id": 42})
    """

    def __init__(self, backend: Optional[TrajectoryRecorderBackend] = None):
        self._backend = backend or JsonLinesBackend()
        self._active_episodes: Dict[str, EpisodeRecord] = {}
        self._lock = threading.Lock()

    def start_episode(
        self,
        *,
        episode_id: str,
        pipeline: str,
        pipeline_version: str = "",
        skill_version: str = "",
        trace_id: str = "",
        user_id_hash: str = "",
        video_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EpisodeRecord:
        """开启一次新的 agent 会话轨迹记录。"""
        episode = EpisodeRecord(
            episode_id=episode_id,
            pipeline=pipeline,
            pipeline_version=pipeline_version,
            skill_version=skill_version,
            status=TrajectoryStatus.RUNNING.value,
            metadata=dict(metadata or {}),
            started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            trace_id=trace_id,
            user_id_hash=user_id_hash,
            video_id=video_id,
        )
        with self._lock:
            self._active_episodes[episode_id] = episode
        logger.debug(
            "trajectory episode started | episode_id=%s | pipeline=%s | trace_id=%s",
            episode_id,
            pipeline,
            trace_id,
        )
        return episode

    def record_step(
        self,
        episode: EpisodeRecord,
        *,
        phase: str,
        action: Dict[str, Any],
        agent_state_before: Optional[Dict[str, Any]] = None,
        agent_state_after: Optional[Dict[str, Any]] = None,
        llm_raw_output: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        validation_result: Optional[Dict[str, Any]] = None,
        governance_decision: Optional[Dict[str, Any]] = None,
        human_feedback: Optional[Dict[str, Any]] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        latency_ms: Optional[float] = None,
    ) -> None:
        """记录单个步骤。"""
        step = StepRecord(
            step_index=len(episode.steps),
            phase=phase,
            action=dict(action),
            agent_state_before=dict(agent_state_before or {}),
            agent_state_after=dict(agent_state_after or {}),
            llm_raw_output=llm_raw_output,
            tool_calls=list(tool_calls or []),
            validation_result=dict(validation_result) if validation_result else None,
            governance_decision=dict(governance_decision) if governance_decision else None,
            human_feedback=dict(human_feedback) if human_feedback else None,
            started_at=started_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            finished_at=finished_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            latency_ms=latency_ms,
        )
        episode.steps.append(step)

        # 异步写入后端（写入失败不中断主流程）
        try:
            self._backend.append_step(episode.episode_id, step)
        except Exception:
            logger.debug("trajectory step append failed (non-fatal)", exc_info=True)

    def add_tool_call_to_last_step(
        self,
        episode: EpisodeRecord,
        *,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        governed: bool = True,
    ) -> None:
        """为最后一步追加工具调用记录（适用于步骤内多个工具串行执行）。"""
        if not episode.steps:
            return
        last_step = episode.steps[-1]
        tool_call = ToolCall(
            name=name,
            params=dict(params or {}),
            result=dict(result) if result else None,
            duration_ms=duration_ms,
            error=error,
            governed=governed,
        )
        last_step.tool_calls.append(tool_call.to_dict())

    def update_step_validation(
        self,
        episode: EpisodeRecord,
        step_index: int,
        validation_result: Dict[str, Any],
    ) -> None:
        """事后更新某步的验证结果。"""
        for step in episode.steps:
            if step.step_index == step_index:
                step.validation_result = dict(validation_result)
                break

    def update_step_governance(
        self,
        episode: EpisodeRecord,
        step_index: int,
        governance_decision: Dict[str, Any],
    ) -> None:
        """事后更新某步的治理决策。"""
        for step in episode.steps:
            if step.step_index == step_index:
                step.governance_decision = dict(governance_decision)
                break

    def set_human_feedback(
        self,
        episode: EpisodeRecord,
        step_index: int,
        feedback: Dict[str, Any],
    ) -> None:
        """设置某步的人工反馈（用于后续标注）。"""
        for step in episode.steps:
            if step.step_index == step_index:
                step.human_feedback = dict(feedback)
                break

    def finish_episode(
        self,
        episode: EpisodeRecord,
        *,
        status: str = TrajectoryStatus.COMPLETED.value,
        final_result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """结束一次 agent 会话轨迹记录。"""
        episode.status = status
        episode.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        episode.final_result = dict(final_result) if final_result else None
        episode.error = error

        if episode.started_at and episode.finished_at:
            try:
                started = datetime.fromisoformat(episode.started_at.replace("Z", "+00:00"))
                finished = datetime.fromisoformat(episode.finished_at.replace("Z", "+00:00"))
                episode.total_latency_ms = (finished - started).total_seconds() * 1000
            except Exception:
                pass

        with self._lock:
            self._active_episodes.pop(episode.episode_id, None)

        try:
            self._backend.save_episode(episode)
        except Exception:
            logger.debug("trajectory episode save failed (non-fatal)", exc_info=True)

        logger.debug(
            "trajectory episode finished | episode_id=%s | status=%s | steps=%d | latency_ms=%.1f",
            episode.episode_id,
            status,
            len(episode.steps),
            episode.total_latency_ms or 0,
        )

    def cancel_episode(self, episode: EpisodeRecord, reason: str = "") -> None:
        """取消并记录一次 agent 会话（用于治理阻断后的清理）。"""
        self.finish_episode(
            episode,
            status=TrajectoryStatus.CANCELLED.value,
            error=f"cancelled: {reason}",
        )

    def get_active_episode(self, episode_id: str) -> Optional[EpisodeRecord]:
        """获取当前活跃的 episode（用于跨步骤上下文传递）。"""
        with self._lock:
            return self._active_episodes.get(episode_id)

    def query(
        self,
        *,
        since: Optional[datetime] = None,
        pipeline: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询历史轨迹（调试/分析用）。"""
        return self._backend.query_episodes(
            since=since,
            pipeline=pipeline,
            status=status,
            limit=limit,
        )


# ---------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------

_recorder: Optional[AgentTrajectoryRecorder] = None
_recorder_lock = threading.Lock()


def get_trajectory_recorder() -> AgentTrajectoryRecorder:
    """获取全局轨迹记录器单例。"""
    global _recorder
    if _recorder is None:
        with _recorder_lock:
            if _recorder is None:
                _recorder = AgentTrajectoryRecorder()
    return _recorder


def reset_recorder_for_tests(backend: Optional[TrajectoryRecorderBackend] = None) -> None:
    """测试用：重置全局记录器并可注入测试后端。"""
    global _recorder
    with _recorder_lock:
        if _recorder is not None:
            if hasattr(_recorder._backend, "flush_all"):
                _recorder._backend.flush_all()
        _recorder = AgentTrajectoryRecorder(backend=backend)
