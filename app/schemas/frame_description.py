"""实时画面描述（Frame Description）Pydantic Schema。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FrameDescriptionRequest(BaseModel):
    """实时画面描述流式请求。

    前端以固定间隔采样视频帧并发送到此端点。
    后端调用 Vinci 模型推理，返回 NDJSON 流式描述。
    """

    video_id: int = Field(..., description="视频 ID")
    user_id: Optional[int] = Field(default=None, description="当前用户 ID")
    # 帧数据：base64 编码的 JPEG 图像列表（避免传输文件路径）
    frames: List[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="当前采样周期的帧列表，每帧为 base64 JPEG 字符串",
    )
    # 视频元信息（用于提示词组装）
    timestamp: float = Field(..., ge=0, description="当前帧对应视频播放位置（秒）")
    video_title: str = Field(default="", description="视频标题（用于上下文提示）")
    # 描述粒度档位：brief=简洁，standard=标准，detailed=详细
    detail_level: Literal["brief", "standard", "detailed"] = Field(
        default="standard",
        description="描述详细度：brief（简洁）/ standard（标准）/ detailed（详细）",
    )
    # 上下文描述历史（最近 N 条，用于短时上下文融合）
    context_history: List[str] = Field(
        default_factory=list,
        description="最近 N 条描述文本，用于上下文融合",
    )
    # 会话 ID（用于追踪同一播放会话）
    session_id: str = Field(default="", description="播放会话 ID，用于追踪同一会话内的描述历史")
    # 是否启用降级模式（Vinci 不可用时返回降级文本而非错误）
    allow_degrade: bool = Field(default=True, description="Vinci 不可用时是否允许降级返回")

    model_config = ConfigDict(str_strip_whitespace=True)


class FrameDescriptionStatusEvent(BaseModel):
    """状态事件：描述服务状态变化。"""

    type: Literal["status"] = "status"
    stage: str = Field(
        ..., description="阶段：connecting / sampling / inferring / fusing / complete / degraded / error"
    )
    message: str = Field(..., description="状态消息（面向用户可读）")
    progress: int = Field(..., ge=0, le=100, description="进度百分比")
    latency_ms: Optional[float] = Field(default=None, description="该阶段耗时（毫秒）")


class FrameDescriptionDeltaEvent(BaseModel):
    """增量描述事件：每个 delta 都是已生成描述文本的增量片断。"""

    type: Literal["description"] = "description"
    delta: str = Field(..., description="描述文本增量片断")
    timestamp: float = Field(..., description="对应视频播放位置（秒）")
    confidence: Optional[float] = Field(default=None, ge=0, le=1, description="置信度 0~1")


class FrameDescriptionCompleteEvent(BaseModel):
    """完成事件：本次采样周期描述已全部生成。"""

    type: Literal["complete"] = "complete"
    full_description: str = Field(..., description="完整描述文本（所有 delta 拼接后的结果）")
    timestamp: float = Field(..., description="对应视频播放位置（秒）")
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    context_summary: Optional[str] = Field(
        default=None,
        description="基于最近上下文的动作/事件进展摘要（无上下文时为 None）",
    )
    degraded: bool = Field(default=False, description="是否为降级描述")
    latency_ms: Optional[float] = Field(default=None, description="端到端推理耗时（毫秒）")


class FrameDescriptionErrorEvent(BaseModel):
    """错误事件：推理失败或服务不可用。"""

    type: Literal["error"] = "error"
    stage: str = Field(..., description="出错阶段：validation / inference / vinci / circuit_breaker / server")
    message: str = Field(..., description="错误消息（面向用户可读）")
    detail: str = Field(..., description="错误详情（供开发/排障用）")
    degraded: bool = Field(default=False, description="是否已降级")
    progress: int = Field(default=100, ge=0, le=100)


class FrameDescriptionSessionRequest(BaseModel):
    """会话级请求：开启/关闭实时描述会话。"""

    video_id: int = Field(..., description="视频 ID")
    user_id: Optional[int] = Field(default=None)
    action: Literal["start", "stop"] = Field(..., description="会话操作：start 开启，stop 关闭")
    detail_level: Literal["brief", "standard", "detailed"] = Field(default="standard")
    session_id: str = Field(default="", description="前端生成的会话 ID（start 时可为空，后端生成）")


class FrameDescriptionSessionResponse(BaseModel):
    """会话响应。"""

    session_id: str = Field(..., description="会话 ID")
    status: str = Field(..., description="会话状态：active / stopped")
    message: str = Field(..., description="状态消息")
    detail_level: str = Field(..., description="当前详细度档位")
