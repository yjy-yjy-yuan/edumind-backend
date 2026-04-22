"""字幕 Pydantic Schema"""

from datetime import datetime
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class SubtitleBase(BaseModel):
    """字幕基础信息"""

    start_time: float
    end_time: float
    text: str
    source: str = "asr"
    language: str = "zh"


class SubtitleCreate(SubtitleBase):
    """创建字幕请求"""

    video_id: int


class SubtitleUpdate(BaseModel):
    """更新字幕请求"""

    text: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None


class SubtitleResponse(SubtitleBase):
    """字幕响应"""

    id: int
    video_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SubtitleListResponse(BaseModel):
    """字幕列表响应"""

    message: str
    subtitles: List[SubtitleResponse]
    total: int


class SubtitleExportRequest(BaseModel):
    """字幕导出请求"""

    format: str = Field(default="srt", description="导出格式: srt, vtt, txt")
