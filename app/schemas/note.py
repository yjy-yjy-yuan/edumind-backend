"""笔记 Pydantic Schema"""

from datetime import datetime
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class NoteTimestampBase(BaseModel):
    """笔记时间戳基础"""

    time_seconds: float
    subtitle_text: Optional[str] = None


class NoteTimestampResponse(NoteTimestampBase):
    """时间戳响应"""

    id: int
    note_id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class NoteCreate(BaseModel):
    """创建笔记请求"""

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    note_type: str = Field(default="text")
    video_id: Optional[int] = None
    tags: Optional[str] = None
    timestamps: Optional[List[NoteTimestampBase]] = None


class NoteUpdate(BaseModel):
    """更新笔记请求"""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    content: Optional[str] = None
    note_type: Optional[str] = None
    video_id: Optional[int] = None
    tags: Optional[str] = None


class NoteResponse(BaseModel):
    """笔记响应"""

    id: int
    title: str
    content: str
    note_type: str
    video_id: Optional[int] = None
    video_title: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    tags: List[str] = []
    keywords: List[str] = []
    timestamps: List[NoteTimestampResponse] = []

    model_config = ConfigDict(from_attributes=True)


class NoteListResponse(BaseModel):
    """笔记列表响应"""

    message: str
    notes: List[NoteResponse]
    total: int
    page: int
    per_page: int


class NoteSimilarRequest(BaseModel):
    """相似笔记查询请求"""

    content: str
    top_k: int = Field(default=5, ge=1, le=20)
