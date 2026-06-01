"""实时画面描述服务（Frame Description Service）。

职责：
1. 接收视频帧（base64 JPEG），调用上游视觉模型服务推理当前画面内容
2. 短时上下文融合：基于最近 N 条描述，输出"正在发生什么"而非单帧孤立结论
3. 相似度去重：避免场景未变化时重复推理
4. 流式输出 NDJSON 事件
5. 降级模式：视觉模型服务不可用时返回降级描述文本
6. 接入集中式遥测管道（可监控）
7. 接入治理审计（安全）
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from time import monotonic, perf_counter
from typing import Any, Callable, Generator, Optional

from PIL import Image
from sqlalchemy.orm import Session

from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent, AnalyticsStatus
from app.core.config import settings
from app.models.subtitle import Subtitle
from app.services.frame_desc.debug import get_frame_description_debug_logger
from app.services.llm_clients.qwen3vl import (
    Qwen3VLClientError,
    Qwen3VLRealtimeClient,
)
from app.services.llm_clients.qwen_vl_cloud import (
    QwenVLCloudClient,
    QwenVLCloudClientError,
)
from app.services.llm_clients.vinci_adapter import (
    VinciAdapterError,
    VinciAdapterService,
)
from app.utils.subtitle_io import repair_mojibake_text

logger = logging.getLogger(__name__)

_frame_desc_debug_logger = None


def _get_frame_desc_debug_logger() -> logging.Logger:
    """Lazy initialization to avoid circular import at module load time."""
    global _frame_desc_debug_logger
    if _frame_desc_debug_logger is None:
        _frame_desc_debug_logger = get_frame_description_debug_logger()
    return _frame_desc_debug_logger


_execute_tool = None
_execute_tool_stream = None


def _get_execute_tool():
    global _execute_tool
    if _execute_tool is None:
        from app.agents.governance.gateway import execute_tool

        _execute_tool = execute_tool
    return _execute_tool


def _get_execute_tool_stream():
    global _execute_tool_stream
    if _execute_tool_stream is None:
        from app.agents.governance.gateway import execute_tool_stream

        _execute_tool_stream = execute_tool_stream
    return _execute_tool_stream


# ----------------------------------------------------------------------
# 异常定义
# ----------------------------------------------------------------------


class FrameDescConfigError(RuntimeError):
    """配置异常：功能未启用或配置缺失。"""


class FrameDescServiceError(RuntimeError):
    """服务异常：推理失败。"""


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------


def _safe_trace_id(tid: Optional[str]) -> str:
    if not tid:
        return settings.ANALYTICS_TRACE_ID_PLACEHOLDER
    return str(tid)[:128]


def _resize_frame_bytes(frame_data: bytes) -> bytes:
    """按配置压缩帧尺寸，降低本地 CPU 视觉模型推理延迟。"""
    max_side = max(64, int(getattr(settings, "FRAME_DESC_MAX_FRAME_SIZE", 640) or 640))
    try:
        with Image.open(io.BytesIO(frame_data)) as image:
            image = image.convert("RGB")
            width, height = image.size
            if width <= 0 or height <= 0:
                return frame_data
            if max(width, height) > max_side:
                ratio = max_side / float(max(width, height))
                image = image.resize(
                    (max(1, int(width * ratio)), max(1, int(height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=80, optimize=True)
            return output.getvalue()
    except Exception as exc:
        logger.debug("帧尺寸压缩跳过 | error=%s", exc)
        return frame_data


def _normalize_frames(raw_frames: list[str]) -> list[bytes]:
    """将 base64 字符串列表解码为模型可消费的 JPEG 字节列表。"""
    result: list[bytes] = []
    max_frames = max(1, int(getattr(settings, "FRAME_DESC_MAX_FRAMES_PER_REQUEST", 1) or 1))
    for item in raw_frames:
        text = str(item or "").strip()
        if not text:
            continue
        if "," in text:
            text = text.split(",", 1)[1]
        try:
            result.append(_resize_frame_bytes(base64.b64decode(text)))
        except Exception as exc:
            logger.debug("帧 base64 解码失败 | error=%s", exc)
        if len(result) >= max_frames:
            break
    return result


def _safe_history(history: list[str]) -> list[str]:
    """裁剪上下文历史，防止 token 溢出。"""
    limit = max(0, int(getattr(settings, "FRAME_DESC_CONTEXT_WINDOW_SIZE", 5) or 5))
    return list(history)[-limit:] if limit > 0 else []


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _compute_text_similarity(text_a: str, text_b: str) -> float:
    """简易相似度：基于字符级 Jaccard 相似度。"""
    if not text_a or not text_b:
        return 0.0
    a_chars = set(str(text_a).lower())
    b_chars = set(str(text_b).lower())
    if not a_chars or not b_chars:
        return 0.0
    intersection = len(a_chars & b_chars)
    union = len(a_chars | b_chars)
    return intersection / union if union > 0 else 0.0


def _format_mmss(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    minutes = total // 60
    remain = total % 60
    return f"{minutes:02d}:{remain:02d}"


def _normalize_subtitle_text(text: str, *, limit: int = 80) -> str:
    compact = " ".join(repair_mojibake_text(str(text or "")).split()).strip()
    if not compact:
        return ""
    return compact if len(compact) <= limit else f"{compact[:limit].rstrip()}..."


def _build_subtitle_fallback_description(
    *,
    db: Optional[Session],
    video_id: int,
    timestamp: float,
    previous: Optional[str],
) -> str:
    """构建字幕驱动的降级描述（不显示"降级"等字样）。"""
    minimal = _build_minimal_safe_description(timestamp=timestamp, previous=previous)
    # 如果有最近的描述，返回字幕内容（自然过渡）
    if db is None or int(video_id or 0) <= 0:
        return minimal

    subtitles = db.query(Subtitle).filter(Subtitle.video_id == int(video_id)).order_by(Subtitle.start_time.asc()).all()
    if not subtitles:
        return minimal

    target = float(timestamp or 0)
    best_index = 0
    best_distance: Optional[float] = None
    for index, item in enumerate(subtitles):
        start_time = float(getattr(item, "start_time", 0) or 0)
        end_time = float(getattr(item, "end_time", start_time) or start_time)
        midpoint = (start_time + end_time) / 2
        distance = abs(midpoint - target)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = index

    fragment_indexes = range(max(0, best_index - 1), min(len(subtitles), best_index + 2))
    fragments: list[str] = []
    for index in fragment_indexes:
        fragment = subtitles[index]
        normalized = _normalize_subtitle_text(getattr(fragment, "text", ""))
        if normalized:
            fragments.append(normalized)

    if not fragments:
        return minimal

    current_time_text = _format_mmss(target)
    subtitle_summary = "；".join(fragments)
    return f"当前约 {current_time_text}，结合附近字幕，讲解内容是：{subtitle_summary}"


def _build_minimal_safe_description(*, timestamp: float, previous: Optional[str] = None) -> str:
    previous_text = str(previous or "").strip()
    if previous_text:
        return previous_text
    return f"当前约 {_format_mmss(timestamp)}，暂时无法获取画面描述或字幕内容，请继续播放后重试。"


def _safe_setting_str(name: str, default: str) -> str:
    value = getattr(settings, name, default)
    if name == "FRAME_DESC_BACKEND" and not isinstance(value, str):
        # Unit tests often patch settings with a MagicMock that does not carry every field.
        # Keep legacy Vinci-path tests stable unless a test explicitly opts into Qwen3-VL.
        return "vinci"
    return str(value if isinstance(value, str) else default).strip() or default


def _safe_setting_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return int(value if isinstance(value, (int, float, str)) else default)


def _build_description_prompt(
    frames_context: str,
    timestamp: float,
    video_title: str,
    detail_level: str,
    context_summary: Optional[str],
) -> str:
    """组装发给视觉模型服务的画面描述提示词。"""
    detail_instruction = {
        "brief": "用 1-2 句话简洁描述当前画面内容。",
        "standard": "用 3-5 句话描述当前画面内容，包含主体动作和关键信息。",
        "detailed": "详细描述当前画面，包括所有可见元素、动作顺序、场景细节和关键信息。",
    }.get(detail_level, "用 3-5 句话描述当前画面内容，包含主体动作和关键信息。")

    title_hint = f"视频主题：{video_title}。" if video_title else ""
    context_hint = f"\n\n近景上下文：{context_summary}" if context_summary else ""
    time_hint = f"\n\n当前播放位置：{timestamp:.1f} 秒。"

    return (
        f"你是一个专业的教育视频画面描述助手。{title_hint}\n"
        f"{detail_instruction}{time_hint}{context_hint}\n\n"
        f"请仅输出画面描述文本，不要输出思考过程，不要输出与画面无关的内容。"
        f'如果画面内容不明确，请标注"可能"，不要编造不存在的内容。'
    )


def _build_fusion_prompt(
    recent_descriptions: list[str],
    current_description: str,
    timestamp: float,
    detail_level: str,
) -> str:
    """组装上下文融合提示词：基于最近描述，生成动作进展摘要。"""
    desc_list = "\n".join(f"- {d}" for d in recent_descriptions[-3:])
    level_hint = {
        "brief": "一句话概括当前动作/事件进展。",
        "standard": "用一到两句话概括当前动作/事件进展，说明「正在发生什么」。",
        "detailed": "详细描述当前动作/事件进展，包括变化趋势和关键转折。",
    }.get(detail_level, "用一到两句话概括当前动作/事件进展。")

    return (
        f"视频画面的最近描述：\n{desc_list}\n\n"
        f"当前最新描述：{current_description}\n\n"
        f"视频时间位置：{timestamp:.1f} 秒\n\n"
        f"任务：{level_hint}\n"
        f"要求：\n"
        f"1. 聚焦「正在发生什么」，而非重复描述单帧\n"
        f"2. 如果动作无明显变化，可输出 None 表示无需额外说明\n"
        f"3. 不要编造或推测未发生的内容\n"
        f"4. 不超过 100 字"
    )


# ----------------------------------------------------------------------
# 熔断状态
# ----------------------------------------------------------------------


@dataclass
class _VinciCircuitBreakerState:
    failure_count: int = 0
    open_until: float = 0.0
    opened_at: float = 0.0
    last_error: str = ""


class _VinciCircuitBreaker:
    """Vinci 熔断器，防止对不可用服务持续发送请求。

    使用 threading.RLock()（可重入锁）保护状态读写。
    """

    _CIRCUITS: dict[str, _VinciCircuitBreakerState] = {}
    _GLOBAL_LOCK = threading.RLock()

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_seconds: float = 30.0,
        key: str = "vinci",
    ):
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_seconds = max(1.0, recovery_seconds)
        self._key = key
        self._clock: Callable[[], float] = monotonic

    def is_blocked(self) -> tuple[bool, bool, float]:
        """返回 (blocked, probe_mode, opened_at)。"""
        now = self._clock()
        with _VinciCircuitBreaker._GLOBAL_LOCK:
            if self._key not in _VinciCircuitBreaker._CIRCUITS:
                _VinciCircuitBreaker._CIRCUITS[self._key] = _VinciCircuitBreakerState()
            s = _VinciCircuitBreaker._CIRCUITS[self._key]

            if s.open_until > now:
                return True, False, s.opened_at
            if s.open_until > 0.0 and now >= s.open_until:
                s.open_until = 0.0
                return False, True, s.opened_at
            return False, False, s.opened_at

    def record_failure(self, error: str) -> tuple[bool, float]:
        """记录失败，返回是否触发了熔断打开。"""
        now = self._clock()
        with _VinciCircuitBreaker._GLOBAL_LOCK:
            if self._key not in _VinciCircuitBreaker._CIRCUITS:
                _VinciCircuitBreaker._CIRCUITS[self._key] = _VinciCircuitBreakerState()
            s = _VinciCircuitBreaker._CIRCUITS[self._key]
            s.failure_count += 1
            s.last_error = str(error)[:100]

            if s.failure_count >= self._failure_threshold:
                s.failure_count = 0
                s.opened_at = now
                s.open_until = now + self._recovery_seconds
                return True, s.opened_at
            return False, 0.0

    def record_success(self) -> bool:
        """成功时重置熔断器，返回是否发生了恢复。"""
        with _VinciCircuitBreaker._GLOBAL_LOCK:
            if self._key not in _VinciCircuitBreaker._CIRCUITS:
                _VinciCircuitBreaker._CIRCUITS[self._key] = _VinciCircuitBreakerState()
            s = _VinciCircuitBreaker._CIRCUITS[self._key]
            recovered = s.opened_at > 0.0 or s.open_until > 0.0
            s.failure_count = 0
            s.open_until = 0.0
            s.opened_at = 0.0
            s.last_error = ""
            return recovered


# ----------------------------------------------------------------------
# 提示词版本化
# ----------------------------------------------------------------------


@dataclass
class PromptTemplate:
    """版本化提示词模板。"""

    version: str
    description: str
    frame_prompt_fn: Callable[..., str] = field(default=_build_description_prompt)
    fusion_prompt_fn: Callable[..., str] = field(default=_build_fusion_prompt)


# 当前版本化管理（未来可扩展为数据库存储/配置中心驱动）
PROMPT_TEMPLATES: dict[str, PromptTemplate] = {
    "v1": PromptTemplate(
        version="v1",
        description="初始版本：标准提示词模板",
    ),
}


def get_active_prompt_template() -> PromptTemplate:
    """获取当前激活的提示词模板。"""
    return PROMPT_TEMPLATES.get("v1", PROMPT_TEMPLATES["v1"])


# ----------------------------------------------------------------------
# 轨迹记录（Compounding 支持）
# ----------------------------------------------------------------------


@dataclass
class FrameDescTrajectory:
    """描述轨迹记录。"""

    session_id: str
    video_id: int
    timestamp: float
    frame_count: int
    description: str
    context_summary: Optional[str]
    degraded: bool
    latency_ms: float
    confidence: Optional[float]
    trace_id: str


_FRAME_DESC_TRAJECTORY_BUFFER: list[FrameDescTrajectory] = []
_FRAME_DESC_TRAJECTORY_LOCK = threading.Lock()
_FRAME_DESC_TRAJECTORY_MAX_BUFFER = 200


def _record_trajectory(entry: FrameDescTrajectory) -> None:
    """将轨迹记录追加到内存缓冲区（未来可导出到 compounding 服务）。"""
    with _FRAME_DESC_TRAJECTORY_LOCK:
        _FRAME_DESC_TRAJECTORY_BUFFER.append(entry)
        if len(_FRAME_DESC_TRAJECTORY_BUFFER) > _FRAME_DESC_TRAJECTORY_MAX_BUFFER:
            _FRAME_DESC_TRAJECTORY_BUFFER.pop(0)


# ----------------------------------------------------------------------
# 核心服务
# ----------------------------------------------------------------------


class FrameDescriptionService:
    """实时画面描述服务。"""

    def __init__(
        self,
        *,
        vinci_adapter: Optional[VinciAdapterService] = None,
        qwen3vl_client: Optional[Qwen3VLRealtimeClient] = None,
        qwen_vl_cloud_client: Optional[QwenVLCloudClient] = None,
        circuit_breaker: Optional[_VinciCircuitBreaker] = None,
    ):
        self._enabled = bool(getattr(settings, "FRAME_DESC_ENABLED", False))
        self._backend = _safe_setting_str("FRAME_DESC_BACKEND", "qwen3vl").lower()
        if self._backend not in {"qwen3vl", "vinci"}:
            logger.warning(
                "unknown frame description backend=%s, fallback to qwen3vl",
                self._backend,
            )
            self._backend = "qwen3vl"
        self._backend_label = "Qwen3-VL" if self._backend == "qwen3vl" else "Vinci"
        self._vinci_adapter = vinci_adapter
        self._qwen3vl_client = qwen3vl_client
        self._qwen_vl_cloud_client = qwen_vl_cloud_client
        self._cb = circuit_breaker or _VinciCircuitBreaker(
            failure_threshold=max(
                1,
                int(getattr(settings, "VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3) or 3),
            ),
            recovery_seconds=max(
                1.0,
                float(getattr(settings, "VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS", 30.0) or 30.0),
            ),
            key=f"frame-desc-{self._backend}",
        )
        self._context_window = max(0, int(getattr(settings, "FRAME_DESC_CONTEXT_WINDOW_SIZE", 5) or 5))
        self._similarity_threshold = max(
            0.0,
            min(
                1.0,
                float(getattr(settings, "FRAME_DESC_SIMILARITY_THRESHOLD", 0.82) or 0.82),
            ),
        )
        self._stable_threshold = max(1, int(getattr(settings, "FRAME_DESC_SCENE_STABLE_THRESHOLD", 4) or 4))
        self._skip_stable_scene = _as_bool(getattr(settings, "FRAME_DESC_SKIP_STABLE_SCENE", False), default=False)
        self._enable_context_fusion = _as_bool(
            getattr(settings, "FRAME_DESC_ENABLE_CONTEXT_FUSION", False),
            default=False,
        )
        self._degraded_interval = max(
            1.0,
            float(getattr(settings, "FRAME_DESC_DEGRADED_INTERVAL_SECONDS", 3.0) or 3.0),
        )
        self._degraded_prefix = str(
            getattr(settings, "FRAME_DESC_DEGRADED_PREFIX", "（描述服务暂不可用，仅供参考）") or ""
        )
        self._timeout = max(1.0, float(getattr(settings, "FRAME_DESC_TIMEOUT_SECONDS", 8.0) or 8.0))
        self._auto_degrade = bool(getattr(settings, "FRAME_DESC_AUTO_DEGRADE", True))
        self._cloud_fallback_enabled = _as_bool(
            getattr(settings, "FRAME_DESC_CLOUD_FALLBACK_ENABLED", False),
            default=False,
        )
        self._cloud_provider = _safe_setting_str("FRAME_DESC_CLOUD_PROVIDER", "qwen").lower()
        self._use_vinci_stream = _as_bool(
            getattr(settings, "FRAME_DESC_USE_VINCI_STREAM", False),
            default=False,
        )
        self._use_qwen3vl_stream = _as_bool(
            getattr(settings, "FRAME_DESC_USE_QWEN3VL_STREAM", False),
            default=False,
        )
        probe_setting = getattr(settings, "FRAME_DESC_PROBE_UPSTREAM_BEFORE_INFER", None)
        if not isinstance(probe_setting, (bool, int, float, str)):
            probe_setting = getattr(settings, "FRAME_DESC_PROBE_VINCI_BEFORE_INFER", True)
        self._probe_before_infer = _as_bool(probe_setting, default=True)
        self._probe_timeout = max(
            0.2,
            float(getattr(settings, "FRAME_DESC_PROBE_TIMEOUT_SECONDS", 0.5) or 0.5),
        )
        self._debug_log_enabled = _as_bool(
            getattr(settings, "FRAME_DESC_DEBUG_LOG", False),
            default=bool(getattr(settings, "DEBUG", False)),
        )
        if self._debug_log_enabled:
            logger.setLevel(logging.DEBUG)
        _get_frame_desc_debug_logger().debug(
            "FrameDescriptionService initialized | FRAME_DESC_BACKEND=%s | backend=%s | enabled=%s | qwen3vl_stream=%s | vinci_stream=%s | cloud_fallback_enabled=%s | cloud_provider=%s",
            getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl"),
            self._backend,
            self._enabled,
            self._use_qwen3vl_stream,
            self._use_vinci_stream,
            self._cloud_fallback_enabled,
            self._cloud_provider,
        )
        if self._backend == "qwen3vl" and vinci_adapter is None:
            _get_frame_desc_debug_logger().debug("VinciAdapterService NOT instantiated | backend=qwen3vl")

        # 会话上下文存储（session_id -> 描述历史）
        self._session_histories: dict[str, list[str]] = {}
        self._session_stable_counts: dict[str, int] = {}
        self._session_lock = threading.Lock()

    def _debug(self, message: str, *args, **kwargs) -> None:
        _get_frame_desc_debug_logger().debug(message, *args, **kwargs)

    def _ensure_session(self, session_id: str) -> None:
        with self._session_lock:
            if session_id not in self._session_histories:
                self._session_histories[session_id] = []
            if session_id not in self._session_stable_counts:
                self._session_stable_counts[session_id] = 0

    def _push_session_history(self, session_id: str, description: str) -> None:
        with self._session_lock:
            if session_id not in self._session_histories:
                self._session_histories[session_id] = []
            self._session_histories[session_id].append(description)
            # 保持窗口大小
            if len(self._session_histories[session_id]) > self._context_window:
                self._session_histories[session_id].pop(0)

    def _get_recent_descriptions(self, session_id: str) -> list[str]:
        with self._session_lock:
            return list(self._session_histories.get(session_id, []))

    def _get_context_summary(self, session_id: str) -> Optional[str]:
        """基于最近描述生成上下文摘要（简单取最近一条）。"""
        recent = self._get_recent_descriptions(session_id)
        if not recent:
            return None
        # 简单策略：取最近一条作为上下文基准
        # 未来可替换为 LLM 融合
        return recent[-1]

    def _check_scene_change(
        self,
        session_id: str,
        current_description: str,
        previous_description: Optional[str],
    ) -> tuple[bool, int]:
        """检查场景是否发生显著变化。返回 (significant_change, stable_count)。"""
        if not previous_description:
            return True, 0

        similarity = _compute_text_similarity(current_description, previous_description)
        with self._session_lock:
            if similarity >= self._similarity_threshold:
                self._session_stable_counts[session_id] = self._session_stable_counts.get(session_id, 0) + 1
            else:
                self._session_stable_counts[session_id] = 0

            stable_count = self._session_stable_counts.get(session_id, 0)
            # 连续 N 次相似度高 -> 场景稳定，无需重复推理
            if stable_count >= self._stable_threshold:
                return False, stable_count
            return True, stable_count

    def _resolve_vinci_adapter(self) -> VinciAdapterService:
        if self._vinci_adapter is not None:
            return self._vinci_adapter
        return VinciAdapterService()

    def _resolve_qwen3vl_client(self) -> Qwen3VLRealtimeClient:
        if self._qwen3vl_client is not None:
            return self._qwen3vl_client
        return Qwen3VLRealtimeClient()

    def _resolve_qwen_vl_cloud_client(self) -> QwenVLCloudClient:
        if self._qwen_vl_cloud_client is not None:
            return self._qwen_vl_cloud_client
        self._qwen_vl_cloud_client = QwenVLCloudClient()
        return self._qwen_vl_cloud_client

    def _can_use_cloud_qwen_vl_fallback(self) -> bool:
        return self._backend == "qwen3vl" and self._cloud_fallback_enabled and self._cloud_provider == "qwen"

    def _call_cloud_qwen_vl_sync(
        self,
        *,
        prompt: str,
        session_id: str,
        trace_id: str,
        base64_frames: Optional[list[str]] = None,
        fallback_reason: str = "",
    ) -> str:
        if not self._can_use_cloud_qwen_vl_fallback():
            raise FrameDescServiceError("cloud_qwen_vl_fallback_disabled")
        safe_frames = [str(f or "").strip() for f in list(base64_frames or []) if f]
        self._debug(
            "fallback_reason=%s | fallback_target=cloud_qwen_vl | session_id=%s | trace_id=%s | base64_frames_count=%d | empty_frames=%s",
            str(fallback_reason or "")[:240],
            session_id,
            trace_id,
            len(safe_frames),
            len(safe_frames) == 0,
        )
        if not safe_frames:
            self._debug(
                "using cloud_qwen_vl text-only mode | session_id=%s | trace_id=%s | base64_frames_count=0",
                session_id,
                trace_id,
            )
        try:
            return self._resolve_qwen_vl_cloud_client().describe(
                base64_frames=safe_frames,
                prompt=prompt,
                session_id=session_id,
                trace_id=trace_id,
            )
        except QwenVLCloudClientError as exc:
            self._debug(
                "cloud_qwen_vl call failed | session_id=%s | trace_id=%s | error=%s",
                session_id,
                trace_id,
                exc,
                exc_info=True,
            )
            raise FrameDescServiceError(f"cloud_qwen_vl_failed:{exc}") from exc

    def _probe_qwen3vl_or_raise(self, *, session_id: str, trace_id: str) -> None:
        if not self._probe_before_infer:
            return
        try:
            health = self._resolve_qwen3vl_client().health_check(timeout_seconds=self._probe_timeout)
            self._debug(
                "qwen3vl health probe | session=%s | trace=%s | reachable=%s | loaded=%s | latency_ms=%s | error_code=%s | error=%s",
                session_id,
                trace_id,
                getattr(health, "reachable", None),
                getattr(health, "loaded", None),
                getattr(health, "latency_ms", None),
                getattr(health, "error_code", None),
                getattr(health, "error", None),
            )
            if not bool(getattr(health, "reachable", False)):
                detail = str(getattr(health, "error", "") or "").strip() or "unreachable"
                error_code = str(getattr(health, "error_code", "") or "").strip() or "QWEN3VL_UNAVAILABLE"
                raise FrameDescServiceError(f"qwen3vl_probe_unreachable:{error_code}:{detail}")
        except FrameDescServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FrameDescServiceError(f"qwen3vl_probe_failed:{exc}") from exc

    def _probe_vinci_or_raise(self, *, session_id: str, trace_id: str) -> None:
        if not self._probe_before_infer:
            return
        try:
            health = self._resolve_vinci_adapter().health_check(timeout_seconds=self._probe_timeout)
            self._debug(
                "vinci health probe | session=%s | trace=%s | reachable=%s | latency_ms=%s | error_code=%s | error=%s",
                session_id,
                trace_id,
                getattr(health, "reachable", None),
                getattr(health, "latency_ms", None),
                getattr(health, "error_code", None),
                getattr(health, "error", None),
            )
            if not bool(getattr(health, "reachable", False)):
                detail = str(getattr(health, "error", "") or "").strip() or "unreachable"
                error_code = str(getattr(health, "error_code", "") or "").strip() or "VINCI_UNAVAILABLE"
                raise FrameDescServiceError(f"vinci_probe_unreachable:{error_code}:{detail}")
        except FrameDescServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FrameDescServiceError(f"vinci_probe_failed:{exc}") from exc

    def _ensure_upstream_reachable(self, *, session_id: str, trace_id: str) -> None:
        if self._backend == "qwen3vl":
            self._probe_qwen3vl_or_raise(session_id=session_id, trace_id=trace_id)
        else:
            self._probe_vinci_or_raise(session_id=session_id, trace_id=trace_id)

    def _emit_telemetry(
        self,
        event_type: str,
        trace_id: str,
        status: AnalyticsStatus,
        latency_ms: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            get_telemetry().emit(
                AnalyticsEvent(
                    event_type=event_type,
                    trace_id=_safe_trace_id(trace_id),
                    module="frame_description",
                    status=status.value,
                    latency_ms=latency_ms,
                    metadata=dict(metadata or {}),
                )
            )
        except Exception:
            logger.debug("frame_description telemetry emit skipped", exc_info=True)

    def _call_vinci_sync(
        self,
        prompt: str,
        session_id: str,
        trace_id: str,
        db,
        *,
        base64_frames: Optional[list[str]] = None,
    ) -> tuple[str, bool]:
        """通过 governance gateway execute_tool 调用 Vinci（防绕过）。

        VinciAdapterService 内部有独立的熔断器（按 VINCI_CIRCUIT_BREAKER_* 配置）。
        本服务也有自己的 _VinciCircuitBreaker，用于"熔断打开 → 跳过推理直接降级"决策。
        两者职责不同，不冲突。

        所有 Vinci 调用必须经 execute_tool 白名单 + 参数校验。

        Args:
            prompt: 当前帧的提示词（含上下文融合内容）
            session_id: 会话 ID
            trace_id: 追踪 ID
            db: 数据库会话（由调用方在外部创建）
            base64_frames: 已编码的图像帧 base64 字符串列表。
                           为空/None 时降级为纯文本模式（silent=True）。
        """
        # 1. 检查本服务层的熔断器：熔断打开时跳过推理，直接降级
        blocked, probe_mode, opened_at = self._cb.is_blocked()
        self._debug(
            "call_vinci_sync start | session=%s | trace=%s | blocked=%s | probe_mode=%s | frames=%d",
            session_id,
            trace_id,
            blocked,
            probe_mode,
            len(base64_frames or []),
        )
        if blocked:
            raise FrameDescServiceError("Vinci circuit breaker is open (service layer)")

        safe_frames = [str(f or "").strip() for f in list(base64_frames or []) if f]
        self._debug(
            "routing to vinci backend | session_id=%s | trace_id=%s | base64_frames_count=%d",
            session_id,
            trace_id,
            len(safe_frames),
        )

        # 推理前快速探测上游可达性，避免连续超时导致前端长时间停留在 connecting。
        self._probe_vinci_or_raise(session_id=session_id, trace_id=trace_id)

        # 2. 通过 governance execute_tool 执行（唯一合法路径）
        try:
            result = _get_execute_tool()(
                "lf_frame_description",
                {
                    "prompt": prompt,
                    "session_id": session_id,
                    "history": [],
                    "trace_id": trace_id,
                    # 核心修复：传递真实视频帧数据
                    "base64_frames": safe_frames,
                },
                db=db,
                trace_id=trace_id,
            )
            # 成功返回
            answer = str(result.get("answer") or result.get("content") or "").strip()
            adapter_degraded = bool(result.get("degraded") is True)
            self._debug(
                "call_vinci_sync done | session=%s | trace=%s | answer_len=%d | adapter_degraded=%s",
                session_id,
                trace_id,
                len(answer),
                adapter_degraded,
            )
            return answer, adapter_degraded
        except VinciAdapterError as exc:
            # adapter 层抛出的所有异常（含 HTTP 错误、超时）都被捕获
            self._cb.record_failure(str(exc))
            self._debug(
                "call_vinci_sync vinci_adapter_error | session=%s | trace=%s | error=%s",
                session_id,
                trace_id,
                exc,
            )
            raise FrameDescServiceError(f"Vinci adapter error: {exc}") from exc

    def _call_qwen3vl_sync(
        self,
        prompt: str,
        session_id: str,
        trace_id: str,
        *,
        base64_frames: Optional[list[str]] = None,
    ) -> tuple[str, bool]:
        blocked, probe_mode, _ = self._cb.is_blocked()
        self._debug(
            "call_qwen3vl_sync start | session=%s | trace=%s | blocked=%s | probe_mode=%s | frames=%d",
            session_id,
            trace_id,
            blocked,
            probe_mode,
            len(base64_frames or []),
        )
        if blocked:
            raise FrameDescServiceError("Qwen3-VL circuit breaker is open (service layer)")

        safe_frames = [str(f or "").strip() for f in list(base64_frames or []) if f]
        self._debug(
            "routing to qwen3vl backend | session_id=%s | trace_id=%s | base64_frames_count=%d",
            session_id,
            trace_id,
            len(safe_frames),
        )
        self._debug(
            "VinciAdapterService NOT instantiated | session_id=%s | trace_id=%s",
            session_id,
            trace_id,
        )
        if not safe_frames:
            self._debug(
                "using qwen3vl text-only mode | session_id=%s | trace_id=%s | base64_frames_count=0",
                session_id,
                trace_id,
            )
        self._probe_qwen3vl_or_raise(session_id=session_id, trace_id=trace_id)

        try:
            answer = self._resolve_qwen3vl_client().describe(
                base64_frames=safe_frames,
                prompt=prompt,
                max_new_tokens=_safe_setting_int("QWEN3VL_MAX_NEW_TOKENS", 48),
            )
            self._debug(
                "call_qwen3vl_sync done | session=%s | trace=%s | answer_len=%d",
                session_id,
                trace_id,
                len(answer),
            )
            return answer, False
        except Qwen3VLClientError as exc:
            self._cb.record_failure(str(exc))
            self._debug(
                "call_qwen3vl_sync error | session=%s | trace=%s | error=%s",
                session_id,
                trace_id,
                exc,
            )
            raise FrameDescServiceError(f"Qwen3-VL adapter error: {exc}") from exc

    def _call_qwen3vl_stream_events(
        self,
        prompt: str,
        session_id: str,
        trace_id: str,
        *,
        base64_frames: Optional[list[str]] = None,
    ) -> Generator[dict[str, Any], None, None]:
        blocked, probe_mode, _ = self._cb.is_blocked()
        self._debug(
            "call_qwen3vl_stream start | session=%s | trace=%s | blocked=%s | probe_mode=%s | frames=%d",
            session_id,
            trace_id,
            blocked,
            probe_mode,
            len(base64_frames or []),
        )
        if blocked:
            raise FrameDescServiceError("Qwen3-VL circuit breaker is open (service layer)")

        safe_frames = [str(f or "").strip() for f in list(base64_frames or []) if f]
        self._debug(
            "routing to qwen3vl backend | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=%d",
            session_id,
            trace_id,
            len(safe_frames),
        )
        self._debug(
            "VinciAdapterService NOT instantiated | stream_mode=start | session_id=%s | trace_id=%s",
            session_id,
            trace_id,
        )
        if not safe_frames:
            self._debug(
                "using qwen3vl text-only mode | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=0",
                session_id,
                trace_id,
            )
        self._probe_qwen3vl_or_raise(session_id=session_id, trace_id=trace_id)

        try:
            for event in self._resolve_qwen3vl_client().stream_describe(
                base64_frames=safe_frames,
                prompt=prompt,
                max_new_tokens=_safe_setting_int("QWEN3VL_MAX_NEW_TOKENS", 48),
            ):
                if str(event.get("event") or "").lower() == "error":
                    error_code = str(event.get("error_code") or "QWEN3VL_STREAM_ERROR")
                    message = str(event.get("message") or error_code)
                    self._cb.record_failure(message)
                    raise FrameDescServiceError(f"{error_code}:{message}")
                self._debug(
                    "stream event | backend=qwen3vl | session_id=%s | trace_id=%s | event_type=%s",
                    session_id,
                    trace_id,
                    str(event.get("event") or ""),
                )
                yield event
            self._debug(
                "stream event end | backend=qwen3vl | session_id=%s | trace_id=%s",
                session_id,
                trace_id,
            )
        except FrameDescServiceError:
            self._debug(
                "qwen3vl stream exception | session_id=%s | trace_id=%s",
                session_id,
                trace_id,
                exc_info=True,
            )
            raise
        except Qwen3VLClientError as exc:
            self._cb.record_failure(str(exc))
            self._debug(
                "qwen3vl stream client exception | session_id=%s | trace_id=%s | error=%s",
                session_id,
                trace_id,
                exc,
                exc_info=True,
            )
            raise FrameDescServiceError(f"Qwen3-VL stream error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            self._cb.record_failure(str(exc))
            self._debug(
                "qwen3vl stream unexpected exception | session_id=%s | trace_id=%s | error=%s",
                session_id,
                trace_id,
                exc,
                exc_info=True,
            )
            raise FrameDescServiceError(f"Qwen3-VL stream error: {exc}") from exc

    def _call_vinci_stream_events(
        self,
        prompt: str,
        session_id: str,
        trace_id: str,
        db,
        *,
        base64_frames: Optional[list[str]] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """通过治理网关流式调用 Vinci internvl SSE。"""
        blocked, probe_mode, _ = self._cb.is_blocked()
        self._debug(
            "call_vinci_stream start | session=%s | trace=%s | blocked=%s | probe_mode=%s | frames=%d",
            session_id,
            trace_id,
            blocked,
            probe_mode,
            len(base64_frames or []),
        )
        if blocked:
            raise FrameDescServiceError("Vinci circuit breaker is open (service layer)")

        safe_frames = [str(f or "").strip() for f in list(base64_frames or []) if f]
        self._debug(
            "routing to vinci backend | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=%d",
            session_id,
            trace_id,
            len(safe_frames),
        )
        self._probe_vinci_or_raise(session_id=session_id, trace_id=trace_id)

        try:
            for event in _get_execute_tool_stream()(
                "lf_frame_description",
                {
                    "prompt": prompt,
                    "session_id": session_id,
                    "history": [],
                    "trace_id": trace_id,
                    "base64_frames": safe_frames,
                },
                db=db,
                trace_id=trace_id,
            ):
                if str(event.get("event") or "").lower() == "error":
                    error_code = str(event.get("error_code") or "VINCI_STREAM_ERROR")
                    message = str(event.get("message") or error_code)
                    self._cb.record_failure(message)
                    raise FrameDescServiceError(f"{error_code}:{message}")
                self._debug(
                    "stream event | backend=vinci | session_id=%s | trace_id=%s | event_type=%s",
                    session_id,
                    trace_id,
                    str(event.get("event") or ""),
                )
                yield event
            self._debug(
                "stream event end | backend=vinci | session_id=%s | trace_id=%s",
                session_id,
                trace_id,
            )
        except FrameDescServiceError:
            self._debug(
                "vinci stream exception | session_id=%s | trace_id=%s",
                session_id,
                trace_id,
                exc_info=True,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self._cb.record_failure(str(exc))
            self._debug(
                "vinci stream unexpected exception | session_id=%s | trace_id=%s | error=%s",
                session_id,
                trace_id,
                exc,
                exc_info=True,
            )
            raise FrameDescServiceError(f"Vinci stream error: {exc}") from exc

    def describe_frames(
        self,
        *,
        frames: list[str],
        timestamp: float,
        video_id: int,
        video_title: str,
        detail_level: str,
        session_id: str,
        trace_id: str,
        context_history: Optional[list[str]] = None,
        allow_degrade: bool = True,
        db=None,
    ) -> Generator[dict[str, Any], None, None]:
        """执行实时画面描述，yield NDJSON 事件流。

        典型事件序列：
        1. status (connecting)
        2. status (inferring)
        3. description (delta) × N
        4. complete
        （或降级/错误时输出相应事件）
        """
        started = perf_counter()
        safe_session_id = str(session_id or "").strip() or str(uuid.uuid4())
        self._ensure_session(safe_session_id)
        self._debug(
            "describe_frames start | session_id=%s | trace_id=%s | FRAME_DESC_BACKEND=%s | backend=%s | video_id=%s | timestamp=%.3f | allow_degrade=%s | base64_frames_count=%d",
            safe_session_id,
            trace_id,
            getattr(settings, "FRAME_DESC_BACKEND", "qwen3vl"),
            self._backend,
            video_id,
            float(timestamp or 0),
            allow_degrade,
            len(frames or []),
        )

        yield {
            "type": "status",
            "stage": "connecting",
            "message": "正在连接画面描述服务",
            "progress": 5,
        }

        # ------------------------------------------------------------------
        # 0. 前置检查
        # ------------------------------------------------------------------
        if not self._enabled:
            yield {
                "type": "error",
                "stage": "config",
                "message": "实时画面描述功能未启用",
                "detail": "FRAME_DESC_ENABLED is False",
                "progress": 100,
                "degraded": False,
            }
            return

        normalized_frames = _normalize_frames(frames)
        self._debug(
            "frames normalized | session_id=%s | trace_id=%s | base64_frames_count=%d | normalized_frames_count=%d | empty_frames=%s",
            safe_session_id,
            trace_id,
            len(frames or []),
            len(normalized_frames),
            len(normalized_frames) == 0,
        )
        if not normalized_frames:
            if self._backend == "qwen3vl":
                self._debug(
                    "using qwen3vl text-only mode | session_id=%s | trace_id=%s | base64_frames_count=0",
                    safe_session_id,
                    trace_id,
                )

        # 将解码后的帧字节转换为 base64 字符串（供视觉模型推理）
        base64_frames = [base64.b64encode(frame_data).decode("ascii") for frame_data in normalized_frames]

        # ------------------------------------------------------------------
        # 1. 场景变化检测（去重）
        # ------------------------------------------------------------------
        recent = self._get_recent_descriptions(safe_session_id)
        previous = recent[-1] if recent else None

        # 相似度检查
        # 注意：这里无法预知描述内容，所以去重需要在推理后进行
        # 但我们可以通过 previous description 的相似度做启发式跳过

        # ------------------------------------------------------------------
        # 2. 构造提示词
        # ------------------------------------------------------------------
        prompt_tpl = get_active_prompt_template()
        context_summary = self._get_context_summary(safe_session_id)

        frame_prompt = prompt_tpl.frame_prompt_fn(
            frames_context=f"[{len(normalized_frames)} frame(s) provided]",
            timestamp=timestamp,
            video_title=video_title,
            detail_level=detail_level,
            context_summary=context_summary,
        )

        # ------------------------------------------------------------------
        # 3. 调用视觉模型服务推理
        # ------------------------------------------------------------------
        degraded = False
        degraded_reason = ""
        description = ""
        streamed_description_sent = False
        infer_latency_ms: Optional[float] = None

        blocked, probe_mode, opened_at = self._cb.is_blocked()
        if blocked:
            degraded_reason = f"{self._backend}_circuit_open"
            if self._can_use_cloud_qwen_vl_fallback():
                try:
                    cloud_started = perf_counter()
                    description = self._call_cloud_qwen_vl_sync(
                        prompt=frame_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        base64_frames=base64_frames,
                        fallback_reason=degraded_reason,
                    )
                    infer_latency_ms = round((perf_counter() - cloud_started) * 1000, 3)
                    degraded = False
                    self._emit_telemetry(
                        "frame_desc_cloud_fallback_used",
                        trace_id,
                        AnalyticsStatus.OK,
                        latency_ms=infer_latency_ms,
                        metadata={
                            "session_id": safe_session_id,
                            "video_id": video_id,
                            "timestamp": timestamp,
                            "fallback_reason": degraded_reason,
                            "frame_count": len(base64_frames),
                        },
                    )
                except FrameDescServiceError as cloud_exc:
                    degraded = True
                    degraded_reason = str(cloud_exc)[:200]
                    description = _build_subtitle_fallback_description(
                        db=db,
                        video_id=video_id,
                        timestamp=timestamp,
                        previous=previous,
                    )
                    self._debug(
                        "fallback_reason=%s | fallback_target=subtitle_description | empty_frames=%s | session_id=%s | trace_id=%s | opened_at=%.3f",
                        degraded_reason,
                        len(base64_frames) == 0,
                        safe_session_id,
                        trace_id,
                        float(opened_at or 0),
                    )
            else:
                degraded = True
                description = _build_subtitle_fallback_description(
                    db=db,
                    video_id=video_id,
                    timestamp=timestamp,
                    previous=previous,
                )
                self._debug(
                    "fallback_reason=%s | fallback_target=subtitle_description | empty_frames=%s | session_id=%s | trace_id=%s | opened_at=%.3f",
                    degraded_reason,
                    len(base64_frames) == 0,
                    safe_session_id,
                    trace_id,
                    float(opened_at or 0),
                )
            self._emit_telemetry(
                "frame_desc_circuit_open",
                trace_id,
                AnalyticsStatus.DEGRADED,
                metadata={
                    "session_id": safe_session_id,
                    "video_id": video_id,
                    "timestamp": timestamp,
                    "frame_count": len(base64_frames),
                },
            )
        else:
            yield {
                "type": "status",
                "stage": "inferring",
                "message": "正在推理画面内容",
                "progress": 25,
            }

            try:
                infer_started = perf_counter()
                adapter_degraded = False
                if self._backend == "qwen3vl" and self._use_qwen3vl_stream:
                    self._debug(
                        "routing to qwen3vl backend | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=%d",
                        safe_session_id,
                        trace_id,
                        len(base64_frames),
                    )
                    stream_events = self._call_qwen3vl_stream_events(
                        prompt=frame_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        base64_frames=base64_frames,
                    )
                    for stream_event in stream_events:
                        stream_type = str(stream_event.get("event") or "").lower()
                        if stream_type == "delta":
                            next_text = str(stream_event.get("delta") or "").strip()
                            if next_text:
                                description = next_text
                                streamed_description_sent = True
                                yield {
                                    "type": "description",
                                    "delta": description,
                                    "timestamp": timestamp,
                                    "confidence": None,
                                }
                        elif stream_type == "done":
                            break
                elif self._backend == "qwen3vl":
                    self._debug(
                        "routing to qwen3vl backend | session_id=%s | trace_id=%s | base64_frames_count=%d",
                        safe_session_id,
                        trace_id,
                        len(base64_frames),
                    )
                    description, adapter_degraded = self._call_qwen3vl_sync(
                        prompt=frame_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        base64_frames=base64_frames,
                    )
                elif self._use_vinci_stream:
                    self._debug(
                        "routing to vinci backend | stream_mode=start | session_id=%s | trace_id=%s | base64_frames_count=%d",
                        safe_session_id,
                        trace_id,
                        len(base64_frames),
                    )
                    for stream_event in self._call_vinci_stream_events(
                        prompt=frame_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        db=db,
                        base64_frames=base64_frames,
                    ):
                        stream_type = str(stream_event.get("event") or "").lower()
                        if stream_type == "delta":
                            next_text = str(stream_event.get("delta") or "").strip()
                            if next_text:
                                description = next_text
                                streamed_description_sent = True
                                yield {
                                    "type": "description",
                                    "delta": description,
                                    "timestamp": timestamp,
                                    "confidence": None,
                                }
                        elif stream_type == "done":
                            break
                else:
                    self._debug(
                        "routing to vinci backend | session_id=%s | trace_id=%s | base64_frames_count=%d",
                        safe_session_id,
                        trace_id,
                        len(base64_frames),
                    )
                    description, adapter_degraded = self._call_vinci_sync(
                        prompt=frame_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        db=db,
                        base64_frames=base64_frames,
                    )
                infer_latency_ms = round((perf_counter() - infer_started) * 1000, 3)

                self._cb.record_success()
                if adapter_degraded:
                    degraded = True
                    degraded_reason = "adapter_degraded_payload"
                    self._debug(
                        "fallback_reason=%s | fallback_target=subtitle_description | empty_frames=%s | session_id=%s | trace_id=%s",
                        degraded_reason,
                        len(base64_frames) == 0,
                        safe_session_id,
                        trace_id,
                    )
                    description = _build_subtitle_fallback_description(
                        db=db,
                        video_id=video_id,
                        timestamp=timestamp,
                        previous=previous,
                    )

                if not description:
                    degraded = True
                    degraded_reason = f"empty_answer_from_{self._backend}"
                    self._debug(
                        "fallback_reason=%s | fallback_target=subtitle_description | empty_frames=%s | session_id=%s | trace_id=%s",
                        degraded_reason,
                        len(base64_frames) == 0,
                        safe_session_id,
                        trace_id,
                    )
                    description = _build_subtitle_fallback_description(
                        db=db,
                        video_id=video_id,
                        timestamp=timestamp,
                        previous=previous,
                    )

            except FrameDescServiceError as exc:
                self._debug(
                    "frame desc inference failed | session_id=%s | video_id=%s | trace_id=%s | error=%s",
                    safe_session_id,
                    video_id,
                    trace_id,
                    exc,
                    exc_info=True,
                )
                if allow_degrade and self._auto_degrade:
                    degraded_reason = str(exc)[:200]
                    if self._can_use_cloud_qwen_vl_fallback():
                        try:
                            cloud_started = perf_counter()
                            description = self._call_cloud_qwen_vl_sync(
                                prompt=frame_prompt,
                                session_id=safe_session_id,
                                trace_id=trace_id,
                                base64_frames=base64_frames,
                                fallback_reason=degraded_reason,
                            )
                            infer_latency_ms = round((perf_counter() - cloud_started) * 1000, 3)
                            degraded = False
                            self._emit_telemetry(
                                "frame_desc_cloud_fallback_used",
                                trace_id,
                                AnalyticsStatus.OK,
                                latency_ms=infer_latency_ms,
                                metadata={
                                    "session_id": safe_session_id,
                                    "video_id": video_id,
                                    "timestamp": timestamp,
                                    "fallback_reason": degraded_reason,
                                    "frame_count": len(base64_frames),
                                },
                            )
                        except FrameDescServiceError as cloud_exc:
                            degraded = True
                            degraded_reason = str(cloud_exc)[:200]
                            self._debug(
                                "fallback_reason=%s | fallback_target=subtitle_description | empty_frames=%s | session_id=%s | trace_id=%s",
                                degraded_reason,
                                len(base64_frames) == 0,
                                safe_session_id,
                                trace_id,
                            )
                            description = _build_subtitle_fallback_description(
                                db=db,
                                video_id=video_id,
                                timestamp=timestamp,
                                previous=previous,
                            )
                            self._emit_telemetry(
                                "frame_desc_inference_degraded",
                                trace_id,
                                AnalyticsStatus.DEGRADED,
                                metadata={
                                    "session_id": safe_session_id,
                                    "video_id": video_id,
                                    "timestamp": timestamp,
                                    "error": str(cloud_exc)[:200],
                                    "primary_error": str(exc)[:200],
                                },
                            )
                    else:
                        degraded = True
                        self._debug(
                            "fallback_reason=%s | fallback_target=subtitle_description | empty_frames=%s | session_id=%s | trace_id=%s",
                            degraded_reason,
                            len(base64_frames) == 0,
                            safe_session_id,
                            trace_id,
                        )
                        description = _build_subtitle_fallback_description(
                            db=db,
                            video_id=video_id,
                            timestamp=timestamp,
                            previous=previous,
                        )
                        self._emit_telemetry(
                            "frame_desc_inference_degraded",
                            trace_id,
                            AnalyticsStatus.DEGRADED,
                            metadata={
                                "session_id": safe_session_id,
                                "video_id": video_id,
                                "timestamp": timestamp,
                                "error": str(exc)[:200],
                            },
                        )
                else:
                    self._debug(
                        "inference failed without degrade | session_id=%s | trace_id=%s | error=%s",
                        safe_session_id,
                        trace_id,
                        exc,
                    )
                    yield {
                        "type": "error",
                        "stage": "inference",
                        "message": "画面描述推理失败",
                        "detail": str(exc)[:500],
                        "progress": 100,
                        "degraded": False,
                    }
                    return

        if degraded:
            yield {
                "type": "status",
                "stage": "subtitle",
                "message": "正在根据字幕内容提供描述",
                "progress": 85,
            }
            self._debug(
                "fallback_target=subtitle_description | session_id=%s | trace_id=%s | fallback_reason=%s | desc_len=%d",
                safe_session_id,
                trace_id,
                degraded_reason,
                len(description),
            )

        # ------------------------------------------------------------------
        # 4. 场景变化检测（推理后去重）
        # ------------------------------------------------------------------
        significant, stable_count = self._check_scene_change(safe_session_id, description, previous)
        if not significant and previous and self._skip_stable_scene:
            # 场景稳定，跳过描述推送（但不跳过记录）
            self._push_session_history(safe_session_id, description)
            total_latency_ms = round((perf_counter() - started) * 1000, 3)
            _record_trajectory(
                FrameDescTrajectory(
                    session_id=safe_session_id,
                    video_id=video_id,
                    timestamp=timestamp,
                    frame_count=len(normalized_frames),
                    description=description,
                    context_summary=None,
                    degraded=degraded,
                    latency_ms=total_latency_ms,
                    confidence=None,
                    trace_id=trace_id,
                )
            )
            yield {
                "type": "complete",
                "stage": "scene_unchanged",
                "full_description": description,
                "timestamp": timestamp,
                "confidence": None,
                "context_summary": None,
                "degraded": degraded,
                "latency_ms": total_latency_ms,
            }
            return

        # ------------------------------------------------------------------
        # 5. 上下文融合（生成动作进展）
        # ------------------------------------------------------------------
        context_summary_out: Optional[str] = None
        if self._enable_context_fusion and recent and len(recent) >= 2 and not degraded:
            yield {
                "type": "status",
                "stage": "fusing",
                "message": "正在融合上下文",
                "progress": 75,
            }
            try:
                fusion_prompt = prompt_tpl.fusion_prompt_fn(
                    recent_descriptions=recent,
                    current_description=description,
                    timestamp=timestamp,
                    detail_level=detail_level,
                )
                if self._backend == "qwen3vl":
                    fused_summary, _ = self._call_qwen3vl_sync(
                        prompt=fusion_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        base64_frames=base64_frames,
                    )
                else:
                    fused_summary, _ = self._call_vinci_sync(
                        prompt=fusion_prompt,
                        session_id=safe_session_id,
                        trace_id=trace_id,
                        db=db,
                        base64_frames=base64_frames,
                    )
                if fused_summary and fused_summary.lower().strip() not in (
                    "none",
                    "无",
                    "暂无",
                ):
                    context_summary_out = fused_summary.strip()
            except Exception as fusion_exc:
                logger.debug("context fusion skipped | error=%s", fusion_exc)
                # 融合失败不影响主流程

        # ------------------------------------------------------------------
        # 6. 输出描述事件
        # ------------------------------------------------------------------
        if not streamed_description_sent:
            yield {
                "type": "description",
                "delta": description,
                "timestamp": timestamp,
                "confidence": None,
            }

        # ------------------------------------------------------------------
        # 7. 完成事件
        # ------------------------------------------------------------------
        total_latency_ms = round((perf_counter() - started) * 1000, 3)

        # 记录轨迹
        _record_trajectory(
            FrameDescTrajectory(
                session_id=safe_session_id,
                video_id=video_id,
                timestamp=timestamp,
                frame_count=len(normalized_frames),
                description=description,
                context_summary=context_summary_out,
                degraded=degraded,
                latency_ms=total_latency_ms,
                confidence=None,
                trace_id=trace_id,
            )
        )

        # 更新会话历史
        self._push_session_history(safe_session_id, description)

        # 遥测
        self._emit_telemetry(
            "frame_desc_completed",
            trace_id,
            AnalyticsStatus.OK if not degraded else AnalyticsStatus.DEGRADED,
            latency_ms=total_latency_ms,
            metadata={
                "session_id": safe_session_id,
                "video_id": video_id,
                "timestamp": timestamp,
                "degraded": degraded,
                "context_fused": context_summary_out is not None,
                "stable_count": stable_count,
                "infer_latency_ms": infer_latency_ms,
            },
        )

        yield {
            "type": "complete",
            "stage": "completed",
            "full_description": description,
            "timestamp": timestamp,
            "confidence": None,
            "context_summary": context_summary_out,
            "degraded": degraded,
            "latency_ms": total_latency_ms,
            "progress": 100,
            "message": "描述已完成",
        }
        self._debug(
            "describe_frames complete | session=%s | trace=%s | degraded=%s | stable_count=%s | latency_ms=%.3f",
            safe_session_id,
            trace_id,
            degraded,
            stable_count,
            total_latency_ms,
        )

    def start_session(
        self,
        video_id: int,
        detail_level: str,
        session_id: str,
    ) -> dict[str, Any]:
        """开启描述会话。"""
        sid = str(session_id or "").strip()
        if not sid:
            sid = str(uuid.uuid4())
        self._ensure_session(sid)
        self._emit_telemetry(
            "frame_desc_session_started",
            trace_id=sid,
            status=AnalyticsStatus.STARTED,
            metadata={"video_id": video_id, "detail_level": detail_level},
        )
        return {
            "session_id": sid,
            "status": "active",
            "message": "实时描述会话已开启",
            "detail_level": detail_level,
        }

    def stop_session(self, session_id: str) -> dict[str, Any]:
        """关闭描述会话。"""
        with self._session_lock:
            self._session_histories.pop(session_id, None)
            self._session_stable_counts.pop(session_id, None)
        self._emit_telemetry(
            "frame_desc_session_stopped",
            trace_id=session_id,
            status=AnalyticsStatus.OK,
            metadata={"session_id": session_id},
        )
        return {
            "session_id": session_id,
            "status": "stopped",
            "message": "实时描述会话已关闭",
            "detail_level": "",
        }

    def get_trajectory_buffer(self) -> list[FrameDescTrajectory]:
        """获取当前轨迹缓冲（用于 compounding 导出）。"""
        with _FRAME_DESC_TRAJECTORY_LOCK:
            return list(_FRAME_DESC_TRAJECTORY_BUFFER)
