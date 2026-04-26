"""学习流智能体 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentExecuteRequest(BaseModel):
    """学习流智能体执行请求。"""

    video_id: Optional[int] = None
    page_context: Literal["video_detail", "qa", "notes"] = Field(default="video_detail")
    current_time_seconds: Optional[float] = Field(default=None, ge=0)
    subtitle_text: str = Field(default="")
    recent_qa_messages: List[dict] = Field(default_factory=list)
    user_input: str = Field(..., min_length=1)
    # user_id 不在前端 payload 中传入，由 auth_deps 从 token 解析
    user_id: Optional[int] = Field(default=None)


class AgentActionRecord(BaseModel):
    """智能体执行记录。"""

    type: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class AgentPlanResponse(BaseModel):
    """学习流智能体响应。"""

    intent: str
    plan: List[str]
    actions: List[str]
    result: dict[str, Any] = Field(default_factory=dict)
    note_id: Optional[int] = None
    video_id: Optional[int] = None
    created_at: datetime
    action_records: List[AgentActionRecord] = Field(default_factory=list)
    episode_id: str = Field(default="", description="Agent 轨迹记录会话 ID")

    model_config = ConfigDict(from_attributes=True)
