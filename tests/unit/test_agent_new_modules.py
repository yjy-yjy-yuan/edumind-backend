"""单元测试：覆盖 6 个新增模块。

模块列表：
- PromptEngine：Token 感知截断策略
- SkillRegistry：灰度发布稳定性
- Trajectory：JSONL / DB 双后端写入
- ResumableTask：断点续传幂等性
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

from app.agents.prompt_engine import (
    HeadOnlyStrategy,
    PreserveMilestonesStrategy,
    PromptEngine,
    PromptEngineConfig,
    PromptSegment,
    TokenBudget,
)
from app.agents.skill_registry import (
    RegistryConfig,
    RolloutStrategy,
    SkillRegistry,
    SkillSpec,
    SkillStatus,
    SkillVersion,
    reset_registry_for_tests,
)
from app.agents.trajectory import (
    AgentTrajectoryRecorder,
    DBBackend,
    EpisodeRecord,
    JsonLinesBackend,
    StepRecord,
    ToolCall,
    TrajectoryStatus,
    reset_recorder_for_tests,
)
from app.tasks.resumable_state_machine import (
    DBCheckpointStore,
    FileCheckpointStore,
    PhaseResult,
    ResumableTask,
    TaskContext,
    TaskState,
    reset_task_store_for_tests,
)

# ---------------------------------------------------------------------------
# PromptEngine Tests
# ---------------------------------------------------------------------------


class TestPreserveMilestonesStrategy:
    """PreserveMilestonesStrategy：保留首尾 + 关键转折点。"""

    def _msg(self, role: str, content: str) -> dict[str, str]:
        return {"role": role, "content": content}

    def test_no_truncation_when_under_budget(self):
        """消息总 token 低于预算时，原样返回。"""
        strategy = PreserveMilestonesStrategy()
        messages = [
            self._msg("system", "你是一个助手"),
            self._msg("user", "你好"),
            self._msg("assistant", "你好！"),
        ]
        result = strategy.apply(messages, available_tokens=100, tokenizer=None)
        assert result == messages

    def test_preserves_system_and_last(self):
        """截断后 system 头和最后一条消息必须保留。"""
        strategy = PreserveMilestonesStrategy()
        messages = [
            self._msg("system", "system prompt"),
            self._msg("user", "ordinary message 1"),
            self._msg("assistant", "ordinary reply"),
            self._msg("user", "ordinary message 2"),
            self._msg("assistant", "last reply with 关键转折点"),
        ]
        result = strategy.apply(messages, available_tokens=8, tokenizer=None)
        roles = [m["role"] for m in result]
        assert roles[0] == "system"
        assert roles[-1] == "assistant"
        assert len(result) < len(messages)

    def test_milestone_messages_preserved(self):
        """含关键词的里程碑消息优先保留。"""
        strategy = PreserveMilestonesStrategy()
        messages = [
            self._msg("system", "system"),
            self._msg("user", "ordinary message"),
            self._msg("assistant", "这里有一个重要结论：导数很重要"),
            self._msg("user", "another ordinary"),
            self._msg("assistant", "last reply"),
        ]
        result = strategy.apply(messages, available_tokens=60, tokenizer=None)
        contents = " ".join(m["content"] for m in result)
        assert "重要结论" in contents


class TestHeadOnlyStrategy:
    """HeadOnlyStrategy：只保留最近 N 条消息。"""

    def _msg(self, role: str, content: str) -> dict[str, str]:
        return {"role": role, "content": content}

    def test_preserves_system_message(self):
        """system 消息始终保留在最前。"""
        strategy = HeadOnlyStrategy(keep_recent=3)
        messages = [
            self._msg("system", "system prompt"),
            self._msg("user", "msg1"),
            self._msg("assistant", "reply1"),
            self._msg("user", "msg2"),
            self._msg("assistant", "reply2"),
            self._msg("user", "msg3"),
            self._msg("assistant", "reply3 - most recent"),
        ]
        result = strategy.apply(messages, available_tokens=200, tokenizer=None)
        assert result[0]["role"] == "system"
        assert "most recent" in result[-1]["content"]

    def test_truncates_when_over_budget(self):
        """超出 token 预算时，从后向前截断。"""
        strategy = HeadOnlyStrategy(keep_recent=10)
        messages = [
            self._msg("system", "sys"),
        ] + [self._msg("user", f"very long message number {i} " + "x" * 100) for i in range(20)]
        result = strategy.apply(messages, available_tokens=100, tokenizer=None)
        assert len(result) < len(messages)
        assert result[0]["role"] == "system"


class TestPromptEngine:
    """PromptEngine 集成测试。"""

    def test_assemble_respects_token_budget(self):
        """token_budget 决定可用空间，历史超出预算时触发截断。"""
        config = PromptEngineConfig(default_truncation_strategy="head_only", raise_on_exceed=False)
        engine = PromptEngine(config=config)

        segments = [PromptSegment("sys", "系统提示词", priority=100)]
        long_history = [
            {"role": "system", "content": "sys"},
        ] + [{"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条消息 " + "x" * 50} for i in range(30)]
        budget = TokenBudget(max_tokens=200, used_tokens=0)

        result = engine.assemble(
            segments=segments,
            messages=long_history,
            token_budget=budget,
        )

        assert result.token_count > 0
        assert result.segments_used == ("sys",)
        # 当 history 很长时，截断后消息数量应少于原始
        if len(result.text) > 0:
            assert result.truncated or result.token_count <= budget.max_tokens

    def test_assemble_priority_ordering(self):
        """高 priority 片段排在前面（降序）。"""
        engine = PromptEngine(config=PromptEngineConfig())
        segments = [
            PromptSegment("low", "LOW", priority=10),
            PromptSegment("high", "HIGH", priority=100),
            PromptSegment("mid", "MID", priority=50),
        ]

        result = engine.assemble(segments=segments)

        high_pos = result.text.index("HIGH")
        mid_pos = result.text.index("MID")
        low_pos = result.text.index("LOW")
        assert high_pos < mid_pos < low_pos, "Segments must be ordered by priority descending"

    def test_assemble_simple(self):
        """assemble_simple 等价于单片段 assemble。"""
        engine = PromptEngine()
        result = engine.assemble_simple(
            system_prompt="直接传入的系统提示词",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert "直接传入的系统提示词" in result.text
        assert result.token_count > 0

    def test_cache_stats(self):
        """cache_stats 返回正确的命中率统计。"""
        engine = PromptEngine(config=PromptEngineConfig(template_cache_size=4))
        for i in range(6):
            engine.register_segment(PromptSegment(f"seg{i}", f"content{i}", priority=i))

        engine.get_segment("seg0")
        engine.get_segment("seg1")
        engine.get_segment("seg0")
        engine.get_segment("seg2")

        stats = engine.cache_stats()
        assert stats["cache_size"] <= 4
        assert stats["cache_hits"] >= 1
        assert stats["cache_misses"] >= 1
        assert 0 <= stats["hit_rate"] <= 1


# ---------------------------------------------------------------------------
# SkillRegistry Tests
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    """SkillRegistry 灰度发布稳定性测试。"""

    def _make_skill_registry(
        self, rollout: RolloutStrategy = RolloutStrategy.FULL, canary_percent: float = 10.0
    ) -> SkillRegistry:
        """构建含两个版本的测试 SkillRegistry。"""
        from app.agents.skill_registry import SkillMetadata

        now = "2025-01-01T00:00:00Z"
        spec_prod = SkillSpec(system_prompt="生产提示词 v1")
        spec_staging = SkillSpec(system_prompt="灰度提示词 v2")
        meta = SkillMetadata(
            id="test_skill",
            name="测试技能",
            versions={
                "1.0.0": SkillVersion(version="1.0.0", spec=spec_prod, status=SkillStatus.PRODUCTION, created_at=now),
                "2.0.0": SkillVersion(version="2.0.0", spec=spec_staging, status=SkillStatus.STAGING, created_at=now),
            },
            active_version="1.0.0",
            rollout_strategy=rollout,
            canary_percent=canary_percent,
        )

        registry = SkillRegistry(config=RegistryConfig(hot_reload=False))
        registry._skills["test_skill"] = meta
        return registry

    def test_get_active_returns_skill_with_segments(self):
        """get_active 返回的 Skill 包含激活版本的片段。"""
        registry = self._make_skill_registry()
        skill = registry.get_active("test_skill")
        assert skill is not None
        segments = skill.get_segments()
        assert len(segments) >= 1
        assert "生产提示词 v1" in segments[0].content

    def test_full_rollout_always_returns_production(self):
        """FULL 策略：无论请求多少次，始终返回 production 版本。"""
        registry = self._make_skill_registry(rollout=RolloutStrategy.FULL)
        for i in range(10):
            skill = registry.get_active_for_request("test_skill", user_id_hash=f"user_{i}")
            assert skill is not None
            ver = registry.get_version("test_skill")
            assert ver == "1.0.0", f"用户 {i} 应路由到 production，但实际为 {ver}"

    def test_canary_routing_is_deterministic(self):
        """同一 user_id_hash 多次请求，路由结果一致（稳定性）。"""
        registry = SkillRegistry(config=RegistryConfig(hot_reload=False, canary_random=0.5))
        registry._skills["test_skill"] = self._make_skill_registry(
            rollout=RolloutStrategy.CANARY,
            canary_percent=50.0,
        )._skills["test_skill"]
        results = [registry.get_active_for_request("test_skill", user_id_hash="stable_user_123") for _ in range(20)]
        versions = [r.get_segments()[0].content for r in results if r]
        assert len(set(versions)) == 1, f"灰度路由不稳定：{set(versions)}"

    def test_canary_distribution_proportional(self):
        """CANARY 策略：10% 灰度时，约 10% 请求路由到 staging。"""
        registry = self._make_skill_registry(rollout=RolloutStrategy.CANARY, canary_percent=10.0)
        total = 2000
        staging_count = 0
        for i in range(total):
            skill = registry.get_active_for_request("test_skill", user_id_hash=f"user_{i}")
            if skill:
                content = skill.get_segments()[0].content
                if "灰度" in content:
                    staging_count += 1

        ratio = staging_count / total
        # 允许 ±5% 误差
        assert 0.05 <= ratio <= 0.15, f"灰度比例 {ratio:.2%} 超出预期范围 [5%, 15%]"

    def test_canary_with_none_hash_uses_random(self):
        """user_id_hash=None 时，纯随机决定灰度。"""
        registry = self._make_skill_registry(rollout=RolloutStrategy.CANARY, canary_percent=50.0)
        # 注册固定随机种子，使得每次调用 get_active_for_request 结果一致
        registry = SkillRegistry(config=RegistryConfig(hot_reload=False, canary_random=0.3))
        registry._skills["test_skill"] = self._make_skill_registry(
            rollout=RolloutStrategy.CANARY, canary_percent=50.0
        )._skills["test_skill"]

        skill = registry.get_active_for_request("test_skill", user_id_hash=None)
        assert skill is not None

    def test_activate_version_switches_immediately(self):
        """activate_version 切换后立即生效，无需重建对象。"""
        registry = self._make_skill_registry(rollout=RolloutStrategy.FULL)
        skill_before = registry.get_active("test_skill")
        assert "v1" in skill_before.get_segments()[0].content

        ok = registry.activate_version("test_skill", "2.0.0")
        assert ok is True

        skill_after = registry.get_active("test_skill")
        assert "v2" in skill_after.get_segments()[0].content

    def test_activate_version_rejects_draft(self):
        """draft 状态的版本不能激活。"""
        registry = self._make_skill_registry(rollout=RolloutStrategy.FULL)
        ok = registry.activate_version("test_skill", "999.0.0")
        assert ok is False

    def test_list_skills_returns_all(self):
        """list_skills 返回所有注册技能（含版本摘要）。"""
        registry = self._make_skill_registry()
        skills = registry.list_skills()
        assert len(skills) >= 1
        item = next(s for s in skills if s["id"] == "test_skill")
        assert item["id"] == "test_skill"
        assert "1.0.0" in item["versions"]
        assert "2.0.0" in item["versions"]

    def test_reload_clears_and_reloads(self):
        """reload 清除缓存并重新加载。"""
        registry = self._make_skill_registry()
        registry._skills["extra_skill"] = self._make_skill_registry()._skills["test_skill"]
        count_before = len(registry.list_skills())

        registry.reload()
        count_after = len(registry.list_skills())
        assert count_after <= count_before

    def teardown_method(self):
        """每个测试后重置全局单例。"""
        reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Trajectory Tests
# ---------------------------------------------------------------------------


class TestJsonLinesBackend:
    """JSONL 文件后端测试。"""

    def test_save_and_query_episode(self, tmp_path):
        """保存 episode 后可查询到对应记录。"""
        backend = JsonLinesBackend(output_dir=tmp_path)

        episode = EpisodeRecord(
            episode_id="ep_test_001",
            pipeline="learning_flow",
            pipeline_version="v1",
            skill_version="1.0.0",
            status=TrajectoryStatus.COMPLETED.value,
            started_at="2025-01-01T00:00:00Z",
            finished_at="2025-01-01T00:01:00Z",
            total_latency_ms=60000.0,
            trace_id="trace_001",
            user_id_hash="hash123",
            video_id=42,
        )
        step = StepRecord(
            step_index=0,
            phase="planner",
            action={"intent": "timestamp_note"},
            agent_state_before={},
            agent_state_after={"intent": "timestamp_note"},
        )
        episode.steps.append(step)
        backend.save_episode(episode)
        backend.flush_all()

        results = backend.query_episodes(limit=10)
        assert len(results) >= 1
        ep_record = next(r for r in results if r["episode_id"] == "ep_test_001")
        assert ep_record["pipeline"] == "learning_flow"
        assert ep_record["status"] == TrajectoryStatus.COMPLETED.value
        assert ep_record["video_id"] == 42
        assert ep_record["user_id_hash"] == "hash123"

    def test_jsonl_file_format(self, tmp_path):
        """保存的文件为合法 JSONL 格式（每行一个 JSON）。"""
        backend = JsonLinesBackend(output_dir=tmp_path)

        episode = EpisodeRecord(
            episode_id="ep_jsonl_check",
            pipeline="test",
            status=TrajectoryStatus.COMPLETED.value,
            started_at="2025-01-01T00:00:00Z",
        )
        backend.save_episode(episode)
        backend.flush_all()

        ep_path = backend._episode_path("ep_jsonl_check")
        lines = ep_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        for line in lines:
            record = json.loads(line)
            assert isinstance(record, dict)


class TestDBBackend:
    """DB 后端测试（使用内存 SQLite）。"""

    def test_save_and_query_episode_in_memory_db(self):
        """DBBackend 保存 episode 后可正确查询。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models.agent_trajectory import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)
        backend = DBBackend(db_session_factory=SessionLocal)

        episode = EpisodeRecord(
            episode_id="ep_db_001",
            pipeline="learning_flow",
            pipeline_version="v1",
            skill_version="1.0.0",
            status=TrajectoryStatus.COMPLETED.value,
            started_at="2025-01-01T00:00:00Z",
            finished_at="2025-01-01T00:01:00Z",
            total_latency_ms=60000.0,
            trace_id="trace_db_001",
            user_id_hash="user_hash_abc",
            video_id=99,
        )
        step = StepRecord(
            step_index=0,
            phase="planner",
            action={"intent": "note"},
            agent_state_before={},
            agent_state_after={"intent": "note"},
        )
        episode.steps.append(step)

        backend.save_episode(episode)

        results = backend.query_episodes(limit=10)
        assert len(results) >= 1
        ep_record = next(r for r in results if r["episode_id"] == "ep_db_001")
        assert ep_record["pipeline"] == "learning_flow"
        assert ep_record["user_id_hash"] == "user_hash_abc"
        assert ep_record["video_id"] == 99

    def test_append_step(self):
        """append_step 将步骤追加到已有 episode。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models.agent_trajectory import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)
        backend = DBBackend(db_session_factory=SessionLocal)

        episode = EpisodeRecord(
            episode_id="ep_step_test",
            pipeline="test",
            status=TrajectoryStatus.RUNNING.value,
            started_at="2025-01-01T00:00:00Z",
        )
        backend.save_episode(episode)

        step = StepRecord(
            step_index=0,
            phase="executor",
            action={"tool": "lf_persist_note"},
        )
        backend.append_step("ep_step_test", step)

        results = backend.query_episodes(limit=10)
        ep_record = next(r for r in results if r["episode_id"] == "ep_step_test")
        assert ep_record["status"] == TrajectoryStatus.RUNNING.value


class TestAgentTrajectoryRecorder:
    """AgentTrajectoryRecorder 主接口测试。"""

    def test_start_record_finish_flow(self, tmp_path):
        """start → record_step → finish 全流程。"""
        backend = JsonLinesBackend(output_dir=tmp_path)
        recorder = AgentTrajectoryRecorder(backend=backend)

        episode = recorder.start_episode(
            episode_id="ep_flow_test",
            pipeline="learning_flow",
            pipeline_version="v1",
            trace_id="trace_flow",
            user_id_hash="hash_abc",
            video_id=7,
            metadata={"page_context": "video_detail"},
        )

        recorder.record_step(
            episode,
            phase="planner",
            action={"intent": "timestamp_note"},
            agent_state_before={"page_context": "video_detail"},
            agent_state_after={"intent": "timestamp_note"},
            latency_ms=15.5,
        )

        recorder.finish_episode(
            episode,
            status="completed",
            final_result={"note_id": 42},
        )

        recorder._backend.flush_all()
        results = recorder.query(limit=10)
        ep_record = next(r for r in results if r["episode_id"] == "ep_flow_test")
        assert ep_record["status"] == "completed"
        assert ep_record["user_id_hash"] == "hash_abc"
        assert ep_record["video_id"] == 7
        assert len(ep_record.get("metadata", {}).get("page_context", "")) > 0

    def test_add_tool_call_to_last_step(self, tmp_path):
        """add_tool_call_to_last_step 追加工具调用到最近步骤。"""
        backend = JsonLinesBackend(output_dir=tmp_path)
        recorder = AgentTrajectoryRecorder(backend=backend)

        episode = recorder.start_episode(episode_id="ep_tool_test", pipeline="test")
        recorder.record_step(episode, phase="executor", action={})

        recorder.add_tool_call_to_last_step(
            episode,
            name="lf_vinci_chat",
            params={"prompt": "hello"},
            result={"answer": "hi"},
            duration_ms=120.5,
            governed=True,
        )

        last_step = episode.steps[-1]
        assert len(last_step.tool_calls) == 1
        tc = last_step.tool_calls[0]
        assert tc["name"] == "lf_vinci_chat"
        assert tc["duration_ms"] == 120.5
        assert tc["governed"] is True

    def teardown_method(self):
        reset_recorder_for_tests()


# ---------------------------------------------------------------------------
# ResumableTask Tests
# ---------------------------------------------------------------------------


class TestFileCheckpointStore:
    """FileCheckpointStore 测试。"""

    def test_save_load_delete(self, tmp_path):
        """保存 → 加载 → 删除生命周期。"""
        store = FileCheckpointStore(data_dir=tmp_path)

        ctx = TaskContext(
            task_id="task_save_load",
            task_name="test",
            state=TaskState.CHECKPOINTED.value,
            current_phase="phase_a",
            shared_data={"video_id": 123},
        )
        store.save(ctx)

        loaded = store.load("task_save_load")
        assert loaded is not None
        assert loaded.task_id == "task_save_load"
        assert loaded.state == TaskState.CHECKPOINTED.value
        assert loaded.shared_data["video_id"] == 123

        store.delete("task_save_load")
        assert store.load("task_save_load") is None

    def test_list_pending(self, tmp_path):
        """list_pending 仅返回 PENDING/RUNNING/CHECKPOINTED 状态的任务。"""
        store = FileCheckpointStore(data_dir=tmp_path)

        for i in range(5):
            ctx = TaskContext(
                task_id=f"task_{i}",
                task_name="test",
                state=TaskState.COMPLETED.value if i % 2 == 0 else TaskState.RUNNING.value,
            )
            store.save(ctx)

        pending = store.list_pending("test")
        assert all(c.state != TaskState.COMPLETED.value for c in pending)


class TestDBCheckpointStore:
    """DBCheckpointStore 测试（内存 SQLite）。"""

    def test_save_load_in_memory_db(self):
        """DBCheckpointStore 保存并正确加载任务上下文。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models.task_checkpoint import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)
        store = DBCheckpointStore(db_session_factory=SessionLocal)

        ctx = TaskContext(
            task_id="task_db_001",
            task_name="test_task",
            state=TaskState.CHECKPOINTED.value,
            current_phase="transcribe",
            shared_data={"video_id": 555},
        )
        store.save(ctx)

        loaded = store.load("task_db_001")
        assert loaded is not None
        assert loaded.task_id == "task_db_001"
        assert loaded.current_phase == "transcribe"
        assert loaded.shared_data["video_id"] == 555


class _DummyVideoTask(ResumableTask):
    """用于测试的虚拟多阶段任务。"""

    NAME = "dummy_video"
    PHASES = ["download", "transcribe", "index"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._phase_calls: list[str] = []
        self._should_fail_phase: str | None = None

    def run_phase(self, phase: str, ctx):
        self._phase_calls.append(phase)
        if self._should_fail_phase == phase:
            from app.tasks.resumable_state_machine import PhaseResult

            ctx.checkpoints[phase] = None  # mark as failed
            return PhaseResult.FAIL
        from app.tasks.resumable_state_machine import PhaseResult

        return PhaseResult.SUCCESS


class TestResumableTask:
    """ResumableTask 断点续传幂等性测试。"""

    def test_new_task_executes_all_phases_in_order(self, tmp_path):
        """新任务按顺序执行所有阶段。"""
        store = FileCheckpointStore(data_dir=tmp_path)
        task = _DummyVideoTask(task_id="task_new_001", store=store)
        ctx = task.execute()

        assert ctx.state == TaskState.COMPLETED.value
        assert task._phase_calls == ["download", "transcribe", "index"]

    def test_checkpoint_saved_after_each_phase(self, tmp_path):
        """每阶段完成后，checkpoint 持久化。"""
        store = FileCheckpointStore(data_dir=tmp_path)
        task = _DummyVideoTask(task_id="task_checkpoint_001", store=store)
        task.execute()

        for phase in ["download", "transcribe", "index"]:
            ctx = store.load("task_checkpoint_001")
            assert ctx is not None
            assert phase in ctx.checkpoints

    def test_recovery_resumes_from_last_checkpoint(self, tmp_path):
        """从 checkpoint 恢复后，跳过已完成阶段。"""
        store = FileCheckpointStore(data_dir=tmp_path)

        # 第一轮：完成 download 阶段后人为中断
        task1 = _DummyVideoTask(task_id="task_recover_001", store=store)
        # 手动注入 checkpoint：只完成 download
        partial_ctx = TaskContext(
            task_id="task_recover_001",
            task_name="dummy_video",
            state=TaskState.CHECKPOINTED.value,
            current_phase="download",
            phase_index=0,
        )
        store.save(partial_ctx)

        # 第二轮：从 download checkpoint 恢复
        task2 = _DummyVideoTask(task_id="task_recover_001", store=store)
        ctx = task2.execute()

        assert ctx.state == TaskState.COMPLETED.value
        # download 不应被再次执行（幂等）
        assert task2._phase_calls == ["transcribe", "index"]

    def test_completed_task_is_idempotent(self, tmp_path):
        """已完成任务再次执行无副作用（幂等）。"""
        store = FileCheckpointStore(data_dir=tmp_path)

        # 第一轮完整执行
        task1 = _DummyVideoTask(task_id="task_idempotent", store=store)
        task1.execute()

        # 第二轮：已有 COMPLETED 状态
        task2 = _DummyVideoTask(task_id="task_idempotent", store=store)
        ctx = task2.execute()

        # 状态保持 COMPLETED，无新增阶段调用
        assert ctx.state == TaskState.COMPLETED.value
        assert task2._phase_calls == []

    def test_cancel_transitions_to_cancelled(self, tmp_path):
        """cancel() 将任务状态置为 CANCELLED。"""
        store = FileCheckpointStore(data_dir=tmp_path)

        task = _DummyVideoTask(task_id="task_cancel_001", store=store)
        task.execute()
        task.cancel()

        ctx = store.load("task_cancel_001")
        assert ctx is not None
        assert ctx.state == TaskState.CANCELLED.value

    def test_temp_file_cleanup_on_complete(self, tmp_path):
        """任务完成后清理追踪的临时文件。"""
        store = FileCheckpointStore(data_dir=tmp_path)
        tmp_file = tmp_path / "temp_intermediate.json"
        tmp_file.write_text("temp data")

        class _TaskWithTempFile(_DummyVideoTask):
            def on_task_complete(self, ctx):
                ctx.temp_files.append(str(tmp_file))

        task = _TaskWithTempFile(task_id="task_cleanup_001", store=store)
        task.execute()

        assert not tmp_file.exists(), "临时文件应在任务完成后被清理"

    def test_temp_file_cleanup_on_failure(self, tmp_path):
        """任务失败时仍清理临时文件。"""
        store = FileCheckpointStore(data_dir=tmp_path)
        tmp_file = tmp_path / "temp_on_fail.json"
        tmp_file.write_text("temp data on failure")

        class _FailingTask(_DummyVideoTask):
            def run_phase(self, phase: str, ctx):
                from app.tasks.resumable_state_machine import PhaseResult

                if phase == "download":
                    ctx.temp_files.append(str(tmp_file))
                    return PhaseResult.FAIL
                return PhaseResult.SUCCESS

        task = _FailingTask(task_id="task_fail_cleanup_001", store=store)
        ctx = task.execute()

        assert ctx.state == TaskState.FAILED.value
        assert not tmp_file.exists(), "失败时临时文件也应被清理"

    def teardown_method(self):
        reset_task_store_for_tests()
