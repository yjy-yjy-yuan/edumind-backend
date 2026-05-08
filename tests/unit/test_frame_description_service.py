"""实时画面描述服务单元测试。"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.agents.governance.context import governance_execution_context
from app.services.frame_description_service import (
    FrameDescriptionService,
    FrameDescServiceError,
    _build_description_prompt,
    _build_fusion_prompt,
    _compute_text_similarity,
    _normalize_frames,
    _safe_history,
    _safe_trace_id,
    _VinciCircuitBreaker,
)
from app.services.qwen3vl_realtime_client import Qwen3VLUnavailableError
from app.services.vinci_adapter_service import VinciAdapterError

# ----------------------------------------------------------------------
# 工具函数测试
# ----------------------------------------------------------------------


class TestComputeTextSimilarity:
    def test_identical_texts(self):
        assert _compute_text_similarity("这是测试", "这是测试") > 0.9

    def test_similar_texts(self):
        sim = _compute_text_similarity(
            "老师在黑板上写公式",
            "老师在黑板上写字",
        )
        assert 0.3 < sim < 1.0

    def test_different_texts(self):
        sim = _compute_text_similarity(
            "数学课堂场景",
            "足球比赛画面",
        )
        assert sim < 0.5

    def test_empty_input(self):
        assert _compute_text_similarity("", "测试") == 0.0
        assert _compute_text_similarity("测试", "") == 0.0
        assert _compute_text_similarity("", "") == 0.0


class TestNormalizeFrames:
    def test_valid_base64_jpeg(self):
        # 1x1 红色 JPEG 的 base64（单行）
        b64 = (
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcp"
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcp"
        )
        frames = _normalize_frames([b64])
        # 可能解码失败，但函数不应崩溃
        assert isinstance(frames, list)

    def test_data_url_with_prefix(self):
        # 有效但可能不完整的 base64 字符串
        b64 = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wB"
        frames = _normalize_frames([b64])
        # 函数不应崩溃，返回列表
        assert isinstance(frames, list)

    def test_invalid_base64_skipped(self):
        frames = _normalize_frames(["not-valid-base64!!!"])
        assert len(frames) == 0

    def test_empty_list(self):
        frames = _normalize_frames([])
        assert frames == []


class TestSafeHistory:
    def test_truncates_to_limit(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(FRAME_DESC_CONTEXT_WINDOW_SIZE=3),
        )
        history = ["a", "b", "c", "d", "e"]
        result = _safe_history(history)
        assert len(result) == 3
        assert result[-1] == "e"


class TestBuildDescriptionPrompt:
    def test_includes_video_title(self):
        prompt = _build_description_prompt(
            frames_context="[1 frame]",
            timestamp=12.5,
            video_title="高等数学",
            detail_level="standard",
            context_summary=None,
        )
        assert "高等数学" in prompt
        assert "12.5" in prompt

    def test_brief_level_short(self):
        prompt = _build_description_prompt(
            frames_context="[1 frame]",
            timestamp=0,
            video_title="",
            detail_level="brief",
            context_summary=None,
        )
        assert "1-2" in prompt or "简洁" in prompt

    def test_detailed_level_long(self):
        prompt = _build_description_prompt(
            frames_context="[1 frame]",
            timestamp=0,
            video_title="",
            detail_level="detailed",
            context_summary=None,
        )
        assert "详细" in prompt

    def test_context_summary_included(self):
        prompt = _build_description_prompt(
            frames_context="[1 frame]",
            timestamp=0,
            video_title="",
            detail_level="standard",
            context_summary="老师正在推导公式",
        )
        assert "老师正在推导公式" in prompt


# ----------------------------------------------------------------------
# 熔断器测试
# ----------------------------------------------------------------------


class TestVinciCircuitBreaker:
    def test_allows_first_requests(self, monkeypatch):
        """初始请求不被阻止。"""
        key = f"test-first-{__name__}-{id(self)}"
        mock_time = [100.0]

        def mock_clock():
            return mock_time[0]

        cb = _VinciCircuitBreaker(failure_threshold=3, recovery_seconds=60.0, key=key)
        cb._clock = mock_clock
        blocked, probe, _ = cb.is_blocked()
        assert blocked is False
        assert probe is False

    def test_opens_after_threshold(self, monkeypatch):
        """连续失败达到阈值后打开熔断器。"""
        key = f"test-threshold-{__name__}-{id(self)}"
        mock_time = [100.0]

        def mock_clock():
            return mock_time[0]

        cb = _VinciCircuitBreaker(failure_threshold=2, recovery_seconds=60.0, key=key)
        cb._clock = mock_clock

        opened1, _ = cb.record_failure("error1")
        assert opened1 is False

        opened2, at2 = cb.record_failure("error2")
        assert opened2 is True
        assert at2 > 0

    def test_circuit_state_tracks_failures(self, monkeypatch):
        """熔断器正确累积失败次数。"""
        key = f"test-failures-{__name__}-{id(self)}"
        cb = _VinciCircuitBreaker(failure_threshold=5, recovery_seconds=60.0, key=key)

        # 失败 3 次（阈值 5），不应打开
        for i in range(3):
            opened, _ = cb.record_failure(f"error{i}")
            assert opened is False

        # 失败 4 次（还未达到阈值）
        opened, _ = cb.record_failure("error4")
        assert opened is False

        # 失败 5 次，达到阈值，熔断器打开
        opened5, at5 = cb.record_failure("error5")
        assert opened5 is True
        assert at5 > 0


# ----------------------------------------------------------------------
# 服务层测试
# ----------------------------------------------------------------------


class TestFrameDescriptionService:
    def test_disabled_returns_config_error(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=False,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
            ),
        )
        service = FrameDescriptionService()

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="test-session",
                trace_id="trace-1",
            )
        )

        # 第一条为 connecting 状态事件，第二条为 config 错误事件
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) >= 1
        assert error_events[0]["stage"] == "config"

    def test_empty_frames_use_qwen3vl_text_only_mode(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_BACKEND="qwen3vl",
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=5,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="（降级）",
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                VINCI_BASE_URL="http://localhost:8010",
                VINCI_API_KEY="",
                VINCI_REQUEST_TIMEOUT_SECONDS=30.0,
                VINCI_CONNECT_TIMEOUT_SECONDS=8.0,
                VINCI_CHAT_PATH="/api/v1/chat",
                VINCI_STREAM_PATH="/api/v1/chat/stream",
                VINCI_STREAM_TIMEOUT_SECONDS=120.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                FRAME_DESC_USE_QWEN3VL_STREAM=False,
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                FRAME_DESC_CLOUD_FALLBACK_ENABLED=False,
                QWEN3VL_MAX_NEW_TOKENS=64,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        class FakeQwenClient:
            def describe(self, *, base64_frames, prompt, max_new_tokens):
                _ = prompt, max_new_tokens
                assert base64_frames == []
                return "文本模式描述"

        service = FrameDescriptionService(qwen3vl_client=FakeQwenClient())

        events = list(
            service.describe_frames(
                frames=[],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="test-session",
                trace_id="trace-1",
            )
        )

        error_events = [e for e in events if e["type"] == "error"]
        complete_events = [e for e in events if e["type"] == "complete"]
        assert error_events == []
        assert len(complete_events) == 1
        assert complete_events[0]["full_description"] == "文本模式描述"

    def test_session_lifecycle(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        service = FrameDescriptionService()

        result = service.start_session(video_id=1, detail_level="standard", session_id="")
        assert result["status"] == "active"
        assert result["session_id"] != ""

        sid = result["session_id"]
        stop_result = service.stop_session(session_id=sid)
        assert stop_result["status"] == "stopped"

    def test_session_reuse_same_history(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=3,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="",
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                VINCI_BASE_URL="http://localhost:8010",
                VINCI_API_KEY="",
                VINCI_REQUEST_TIMEOUT_SECONDS=30.0,
                VINCI_CONNECT_TIMEOUT_SECONDS=8.0,
                VINCI_CHAT_PATH="/api/v1/chat",
                VINCI_STREAM_PATH="/api/v1/chat/stream",
                VINCI_STREAM_TIMEOUT_SECONDS=120.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )

        def fake_execute_tool(tool_name, params, *, db, trace_id):
            _ = db, trace_id, tool_name
            return {"answer": "老师在黑板上写字"}

        monkeypatch.setattr(
            "app.services.frame_description_service.execute_tool",
            fake_execute_tool,
        )

        # 覆盖 vinci_adapter 使其返回固定 answer
        service = FrameDescriptionService()
        service._cb = _VinciCircuitBreaker(failure_threshold=3, recovery_seconds=30.0, key=f"test-history-{id(self)}")

        session_id = "test-history-session"

        # 第一次调用
        events1 = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=5.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id=session_id,
                trace_id="trace-1",
                db=None,
            )
        )
        complete1 = next(e for e in events1 if e["type"] == "complete")
        assert complete1["full_description"] == "老师在黑板上写字"

        # 第二次调用：验证历史已积累
        recent = service._get_recent_descriptions(session_id)
        assert len(recent) == 1
        assert "老师" in recent[0]

    def test_vinci_streaming_description_uses_delta_events(self, monkeypatch):
        """开启流式模式后，后端应透传 Vinci 视觉流增量并以最终文本 complete。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=3,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="",
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                FRAME_DESC_USE_VINCI_STREAM=True,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                FRAME_DESC_USE_QWEN3VL_STREAM=True,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        def fake_execute_tool_stream(tool_name, params, *, db, trace_id):
            _ = tool_name, params, db, trace_id
            yield {"event": "delta", "delta": "老师正在讲解"}
            yield {"event": "delta", "delta": "老师正在讲解例题"}
            yield {"event": "done"}

        monkeypatch.setattr(
            "app.services.frame_description_service.execute_tool_stream",
            fake_execute_tool_stream,
        )

        service = FrameDescriptionService()
        service._cb = _VinciCircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            key=f"test-streaming-{id(self)}",
        )

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=8.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="stream-session",
                trace_id="trace-stream",
                db=None,
            )
        )

        description_events = [e for e in events if e["type"] == "description"]
        assert [e["delta"] for e in description_events] == [
            "老师正在讲解",
            "老师正在讲解例题",
        ]
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["full_description"] == "老师正在讲解例题"
        assert complete["degraded"] is False

    def test_qwen3vl_streaming_description_uses_delta_events(self, monkeypatch):
        """Qwen3-VL 后端应直接消费新服务 SSE，并生成完整 complete 事件。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_BACKEND="qwen3vl",
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=3,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="",
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                QWEN3VL_MAX_NEW_TOKENS=64,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                FRAME_DESC_USE_QWEN3VL_STREAM=True,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        class FakeQwenClient:
            def stream_describe(self, *, base64_frames, prompt, max_new_tokens):
                _ = base64_frames, prompt, max_new_tokens
                yield {"event": "delta", "delta": "老师正在演示"}
                yield {"event": "delta", "delta": "老师正在演示实验"}
                yield {"event": "done"}

        service = FrameDescriptionService(qwen3vl_client=FakeQwenClient())
        service._cb = _VinciCircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            key=f"test-qwen-streaming-{id(self)}",
        )

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=8.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="qwen-stream-session",
                trace_id="trace-qwen-stream",
                db=None,
            )
        )

        description_events = [e for e in events if e["type"] == "description"]
        assert [e["delta"] for e in description_events] == [
            "老师正在演示",
            "老师正在演示实验",
        ]
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["full_description"] == "老师正在演示实验"
        assert complete["degraded"] is False

    def test_qwen3vl_default_uses_sync_description(self, monkeypatch):
        """Qwen3-VL 默认走非流式端点，避免 CPU 首 token 慢导致前端空闲超时。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_BACKEND="qwen3vl",
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=3,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="",
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                QWEN3VL_MAX_NEW_TOKENS=64,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                FRAME_DESC_USE_QWEN3VL_STREAM=False,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        class FakeQwenClient:
            def describe(self, *, base64_frames, prompt, max_new_tokens):
                _ = base64_frames, prompt, max_new_tokens
                return "老师正在演示实验"

            def stream_describe(self, **_kwargs):
                raise AssertionError("stream_describe should not be used by default")

        service = FrameDescriptionService(qwen3vl_client=FakeQwenClient())
        service._cb = _VinciCircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            key=f"test-qwen-sync-{id(self)}",
        )

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=8.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="qwen-sync-session",
                trace_id="trace-qwen-sync",
                db=None,
            )
        )

        description_events = [e for e in events if e["type"] == "description"]
        assert [e["delta"] for e in description_events] == ["老师正在演示实验"]
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["degraded"] is False


# ----------------------------------------------------------------------
# 降级流测试
# ----------------------------------------------------------------------


class TestFrameDescriptionDegradedMode:
    def test_vinci_adapter_degraded_payload_marks_complete_as_degraded(self, monkeypatch):
        """当 execute_tool 返回 degraded=True 时，complete 事件也必须标记 degraded=True。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=5,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="（描述服务暂不可用，仅供参考）",
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        def fake_execute_tool(tool_name, params, *, db, trace_id):
            _ = db, trace_id, tool_name, params
            return {
                "answer": "Vinci 服务暂不可用，已返回降级结果，请稍后重试。",
                "degraded": True,
                "session_id": "degraded-payload-session",
            }

        monkeypatch.setattr(
            "app.services.frame_description_service.execute_tool",
            fake_execute_tool,
        )

        service = FrameDescriptionService()
        service._cb = _VinciCircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            key=f"test-adapter-degraded-{id(self)}",
        )

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="degraded-payload-session",
                trace_id="trace-degraded-payload",
                allow_degrade=True,
                db=None,
            )
        )

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["degraded"] is True

    def test_vinci_unavailable_returns_degraded(self, monkeypatch):
        """当 Vinci 超时时（execute_tool 抛出 VinciAdapterError），服务应返回降级描述。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=5,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="（描述服务暂不可用，仅供参考）",
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        unique_key = f"test-degraded-{id(self)}"
        cb = _VinciCircuitBreaker(failure_threshold=3, recovery_seconds=30.0, key=unique_key)

        # execute_tool 在 Vinci 不可用时抛出 VinciAdapterError
        def fake_execute_tool(tool_name, params, *, db, trace_id):
            _ = db, trace_id, tool_name, params
            raise VinciAdapterError(
                message="connection timeout",
                error_code="VINCI_TIMEOUT",
                trace_id="trace-degraded",
                status_code=504,
            )

        monkeypatch.setattr(
            "app.services.frame_description_service.execute_tool",
            fake_execute_tool,
        )

        service = FrameDescriptionService()
        service._cb = cb

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="degraded-session",
                trace_id="trace-degraded",
                allow_degrade=True,
                db=None,
            )
        )

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["degraded"] is True

    def test_qwen3vl_unavailable_returns_degraded(self, monkeypatch):
        """当 Qwen3-VL 不可用时，实时描述必须返回 degraded complete 事件。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_BACKEND="qwen3vl",
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=5,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="（描述服务暂不可用，仅供参考）",
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                QWEN3VL_MAX_NEW_TOKENS=64,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                FRAME_DESC_USE_QWEN3VL_STREAM=False,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        class FakeQwenClient:
            def describe(self, *, base64_frames, prompt, max_new_tokens):
                _ = base64_frames, prompt, max_new_tokens
                raise Qwen3VLUnavailableError("connection refused")

        service = FrameDescriptionService(qwen3vl_client=FakeQwenClient())
        service._cb = _VinciCircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            key=f"test-qwen-degraded-{id(self)}",
        )

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="qwen-degraded-session",
                trace_id="trace-qwen-degraded",
                allow_degrade=True,
                db=None,
            )
        )

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["degraded"] is True
        assert "暂时无法获取画面描述" in complete_events[0]["full_description"]

    def test_qwen3vl_unavailable_uses_cloud_qwen_vl_before_subtitle(self, monkeypatch):
        """本地 Qwen3-VL 不可用时，先使用 Cloud Qwen-VL，不直接进入字幕降级。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_BACKEND="qwen3vl",
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=5,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="（描述服务暂不可用，仅供参考）",
                FRAME_DESC_PROBE_VINCI_BEFORE_INFER=False,
                FRAME_DESC_CLOUD_FALLBACK_ENABLED=True,
                FRAME_DESC_CLOUD_PROVIDER="qwen",
                QWEN3VL_MAX_NEW_TOKENS=64,
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=True,
                FRAME_DESC_USE_QWEN3VL_STREAM=False,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        class FakeQwenClient:
            def describe(self, *, base64_frames, prompt, max_new_tokens):
                _ = base64_frames, prompt, max_new_tokens
                raise Qwen3VLUnavailableError("connection refused")

        class FakeCloudClient:
            def describe(self, *, base64_frames, prompt, session_id, trace_id):
                _ = base64_frames, prompt, session_id, trace_id
                return "云端 Qwen-VL 已完成画面描述"

        service = FrameDescriptionService(
            qwen3vl_client=FakeQwenClient(),
            qwen_vl_cloud_client=FakeCloudClient(),
        )

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="qwen-cloud-fallback-session",
                trace_id="trace-qwen-cloud-fallback",
                allow_degrade=True,
                db=None,
            )
        )

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["degraded"] is False
        assert complete_events[0]["full_description"] == "云端 Qwen-VL 已完成画面描述"

    def test_degrade_disabled_raises_error(self, monkeypatch):
        """当 allow_degrade=False 且 Vinci 不可用时，应返回错误事件。"""
        monkeypatch.setattr(
            "app.services.frame_description_service.get_telemetry",
            lambda: MagicMock(emit=MagicMock()),
        )
        monkeypatch.setattr(
            "app.services.frame_description_service.settings",
            MagicMock(
                FRAME_DESC_ENABLED=True,
                FRAME_DESC_TIMEOUT_SECONDS=8.0,
                FRAME_DESC_CONTEXT_WINDOW_SIZE=5,
                FRAME_DESC_SIMILARITY_THRESHOLD=0.82,
                FRAME_DESC_SCENE_STABLE_THRESHOLD=4,
                FRAME_DESC_DEGRADED_INTERVAL_SECONDS=10.0,
                FRAME_DESC_DEGRADED_PREFIX="",
                VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
                VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30.0,
                FRAME_DESC_AUTO_DEGRADE=False,
                ANALYTICS_TRACE_ID_PLACEHOLDER="unset",
            ),
        )

        unique_key = f"test-no-degrade-{id(self)}"
        cb = _VinciCircuitBreaker(failure_threshold=3, recovery_seconds=30.0, key=unique_key)

        def fake_execute_tool(tool_name, params, *, db, trace_id):
            _ = db, trace_id, tool_name, params
            raise VinciAdapterError(
                message="connection timeout",
                error_code="VINCI_TIMEOUT",
                trace_id="trace-no-degrade",
                status_code=504,
            )

        monkeypatch.setattr(
            "app.services.frame_description_service.execute_tool",
            fake_execute_tool,
        )

        service = FrameDescriptionService()
        service._cb = cb

        events = list(
            service.describe_frames(
                frames=["/9j/4AAQSkZJRg=="],
                timestamp=10.0,
                video_id=1,
                video_title="Test",
                detail_level="standard",
                session_id="no-degrade-session",
                trace_id="trace-no-degrade",
                allow_degrade=False,
                db=None,
            )
        )

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["degraded"] is False


# ----------------------------------------------------------------------
# 提示词版本化测试
# ----------------------------------------------------------------------


class TestPromptTemplates:
    def test_active_template_available(self):
        from app.services.frame_description_service import get_active_prompt_template

        tpl = get_active_prompt_template()
        assert tpl.version == "v1"
        assert tpl.description == "初始版本：标准提示词模板"

    def test_fusion_prompt_includes_history(self):
        from app.services.frame_description_service import get_active_prompt_template

        tpl = get_active_prompt_template()
        prompt = tpl.fusion_prompt_fn(
            recent_descriptions=["老师在黑板上写字", "老师继续推导公式"],
            current_description="老师完成公式推导",
            timestamp=30.0,
            detail_level="standard",
        )
        assert "老师在黑板上写字" in prompt
        assert "老师完成公式推导" in prompt
        assert "30.0" in prompt
