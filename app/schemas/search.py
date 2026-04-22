"""搜索相关的数据结构 - Pydantic Schema"""

from datetime import datetime
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class SemanticSearchRequest(BaseModel):
    """语义搜索请求"""

    query: str = Field(..., description="自然语言查询", max_length=500)
    video_ids: Optional[List[int]] = Field(None, description="限定搜索的视频 ID 列表（为空时搜索所有用户视频）")
    limit: int = Field(10, description="返回结果数", ge=1, le=100)
    threshold: float = Field(0.5, description="相似度阈值", ge=0.0, le=1.0)


class SearchResultChunk(BaseModel):
    """搜索结果中的单个片段"""

    video_id: int
    video_title: Optional[str] = Field(None, description="视频标题")
    chunk_id: str
    start_time: float = Field(..., description="片段在原视频中的起始时间（秒）")
    end_time: float = Field(..., description="片段在原视频中的结束时间（秒）")
    similarity_score: float = Field(..., description="与查询的余弦相似度（0-1）", ge=0.0, le=1.0)
    preview_text: Optional[str] = Field(None, description="对应的字幕预览文本")


class SemanticSearchResponse(BaseModel):
    """语义搜索响应"""

    query: str
    results: List[SearchResultChunk]
    total_time_ms: int = Field(..., description="搜索耗时（毫秒）")
    message: Optional[str] = None


class IndexingStatusResponse(BaseModel):
    """索引状态响应"""

    video_id: int
    status: str = Field(..., description="状态：pending/processing/completed/failed/not_indexed")
    progress: int = Field(..., description="进度百分比", ge=0, le=100)
    chunk_count: int = Field(..., description="已处理的片段数量")
    indexed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    message: Optional[str] = None


class VideoClipExtractionRequest(BaseModel):
    """视频片段提取请求"""

    video_id: int
    start_time: float = Field(..., description="起始时间（秒）", ge=0)
    end_time: float = Field(..., description="结束时间（秒）", gt=0)
    output_format: Optional[str] = Field("mp4", description="输出格式")


class VideoClipResponse(BaseModel):
    """视频片段响应"""

    video_id: int
    start_time: float
    end_time: float
    clip_path: str = Field(..., description="生成的片段文件路径")
    duration: float = Field(..., description="片段时长（秒）")
