"""问答 Pydantic Schema"""

from datetime import datetime
from typing import List
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class QAHistoryMessage(BaseModel):
    """问答历史消息。"""

    role: str = Field(..., description="消息角色: user, assistant")
    content: str = Field(..., min_length=1, description="消息内容")


class AskRequest(BaseModel):
    """问答请求"""

    user_id: Optional[int] = Field(default=None, description="当前用户ID，用于隔离问答空间")
    video_id: Optional[int] = None
    question: str = Field(..., min_length=1)
    mode: str = Field(default="video", description="问答模式: video, free")
    stream: bool = Field(default=False, description="是否流式返回")
    # 对话模式（前端新接口）：direct=直接回答，deep_think=深度思考
    # 优先级高于 provider + deep_thinking
    chat_mode: Literal["direct", "deep_think"] = Field(default=None, description="对话模式: direct, deep_think")
    # 以下字段保留，向后兼容；chat_mode 存在时会被忽略
    provider: str = Field(default="qwen", min_length=1, description="模型提供方: qwen, deepseek")
    model: Optional[str] = Field(default=None, description="可选模型名称，未传时按 provider 使用默认模型")
    deep_thinking: bool = Field(default=False, description="是否启用深度思考（向后兼容）")
    history: List[QAHistoryMessage] = Field(default_factory=list, description="最近问答历史，用于连续追问")


class QuestionResponse(BaseModel):
    """问题响应"""

    id: int
    user_id: Optional[int] = None
    video_id: Optional[int] = None
    provider: Optional[str] = None
    mode: Optional[str] = None
    model: Optional[str] = None
    content: str
    answer: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class QuestionHistoryResponse(BaseModel):
    """问答历史响应"""

    message: str
    questions: list[QuestionResponse]
    messages: List[dict] = Field(default_factory=list)
    total: int
