"""Sleek 设计能力相关 Schema。"""

from __future__ import annotations

from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class DesignProjectCreateRequest(BaseModel):
    """创建设计项目请求。"""

    name: str = Field(..., min_length=1, max_length=120, description="Sleek 项目名称")


class DesignMessageRequest(BaseModel):
    """发送设计描述请求。"""

    message: str = Field(..., min_length=1, description="自然语言设计描述")
    image_urls: List[str] = Field(default_factory=list, description="可选的参考图 URL 列表")
    target_screen_id: Optional[str] = Field(default=None, description="可选的目标 screenId")
    wait: bool = Field(default=True, description="是否由后端等待直到设计任务完成")
    idempotency_key: Optional[str] = Field(default=None, description="幂等键，便于重试")
    include_screenshots: bool = Field(default=True, description="任务完成后是否自动生成截图预览")


class DesignScreenshotRequest(BaseModel):
    """截图预览请求。"""

    component_ids: List[str] = Field(..., min_length=1, description="待渲染的 componentId 列表")
    format: str = Field(default="png", description="截图格式：png / webp")
    scale: int = Field(default=2, ge=1, le=3, description="截图缩放倍率")
    background: str = Field(default="transparent", description="背景色")
    show_dots: bool = Field(default=False, description="是否显示背景点阵")
