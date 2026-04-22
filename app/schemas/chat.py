"""聊天 Pydantic Schema"""

from typing import List
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class ChatMessage(BaseModel):
    """聊天消息"""

    role: str = Field(..., description="消息角色: user, assistant, system")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    """聊天请求"""

    messages: List[ChatMessage]
    stream: bool = Field(default=True, description="是否流式返回")
    # 对话模式: direct=直接回答(通义千问主用,DeepSeek兜底), deep_think=深度思考(DeepSeek reasoner)
    mode: Literal["direct", "deep_think"] = Field(default="direct", description="对话模式")
    # 保留 provider 和 model 字段用于内部路由，不再对用户暴露
    provider: str = Field(default="qwen", description="模型提供方: qwen, deepseek (内部使用)")
    model: Optional[str] = None


class ChatResponse(BaseModel):
    """聊天响应 (非流式)"""

    message: str
    content: str
