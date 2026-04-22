"""视频模型 - SQLAlchemy 2.0 版本"""

import json
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from typing import List
from typing import Optional

from app.models.base import Base
from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

if TYPE_CHECKING:
    from app.models.subtitle import Subtitle


class VideoStatus(str, Enum):
    """视频状态枚举"""

    UPLOADED = "uploaded"  # 已上传
    PENDING = "pending"  # 等待处理
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"  # 处理完成
    FAILED = "failed"  # 处理失败
    DOWNLOADING = "downloading"  # 下载中（用于视频链接上传）


class VideoProcessingOrigin(str, Enum):
    """视频处理来源枚举"""

    ONLINE_BACKEND = "online_backend"
    IOS_OFFLINE = "ios_offline"


class Video(Base):
    """视频模型"""

    __tablename__ = "videos"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 用户关联
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 基本信息
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    filepath: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    md5: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # 处理后的文件
    processed_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    processed_filepath: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    preview_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    preview_filepath: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subtitle_filepath: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 状态信息
    status: Mapped[VideoStatus] = mapped_column(SQLEnum(VideoStatus), default=VideoStatus.UPLOADED)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 时间戳
    upload_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 进度信息
    process_progress: Mapped[float] = mapped_column(Float, default=0.0)
    current_step: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 视频属性
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    frame_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 内容分析
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 字符串
    processing_origin: Mapped[VideoProcessingOrigin] = mapped_column(
        SQLEnum(VideoProcessingOrigin),
        default=VideoProcessingOrigin.ONLINE_BACKEND,
        nullable=False,
    )

    # 语义搜索索引
    has_semantic_index: Mapped[bool] = mapped_column(default=False)
    vector_index_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 关系
    subtitles: Mapped[List["Subtitle"]] = relationship("Subtitle", back_populates="video", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Video {self.title or self.filename}>"

    def get_upload_source(self) -> str:
        """上传来源标识：本地文件或链接导入"""
        if self.processing_origin == VideoProcessingOrigin.IOS_OFFLINE:
            return "ios_offline"
        return "url_import" if self.url else "local_file"

    def get_upload_source_label(self) -> str:
        """上传来源中文标签"""
        if self.processing_origin == VideoProcessingOrigin.IOS_OFFLINE:
            return "iOS 离线处理"
        return "链接导入" if self.url else "本地上传"

    def get_upload_source_value(self) -> Optional[str]:
        """上传来源值：链接导入保存 URL，本地上传保存文件名"""
        if self.url:
            return self.url
        return self.filename

    def to_dict(self) -> dict:
        """转换为字典表示"""
        return {
            "id": self.id,
            "filename": self.filename,
            "filepath": self.filepath,
            "title": self.title,
            "url": self.url,
            "status": self.status.value if isinstance(self.status, VideoStatus) else self.status,
            "upload_time": self.upload_time.isoformat() if self.upload_time else None,
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "frame_count": self.frame_count,
            "md5": self.md5,
            "summary": self.summary,
            "tags": json.loads(self.tags) if self.tags else [],
            "preview_filename": self.preview_filename,
            "subtitle_filepath": self.subtitle_filepath,
            "process_progress": self.process_progress,
            "current_step": self.current_step,
            "task_id": self.task_id,
            "error_message": self.error_message,
            "processing_origin": (
                self.processing_origin.value
                if isinstance(self.processing_origin, VideoProcessingOrigin)
                else self.processing_origin
            ),
            "processing_origin_label": (
                "iOS 离线处理" if self.processing_origin == VideoProcessingOrigin.IOS_OFFLINE else "在线处理"
            ),
            "upload_source": self.get_upload_source(),
            "upload_source_label": self.get_upload_source_label(),
            "upload_source_value": self.get_upload_source_value(),
        }
