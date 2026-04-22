"""笔记模型 - SQLAlchemy 2.0 版本"""

from datetime import datetime
from typing import TYPE_CHECKING
from typing import List
from typing import Optional

from app.models.base import Base
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

if TYPE_CHECKING:
    from app.models.video import Video


class Note(Base):
    """笔记模型"""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_vector: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 内容向量（JSON格式）
    note_type: Mapped[str] = mapped_column(String(50), default="text")  # text, code, list等
    video_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("videos.id"), nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 标签和关键词
    tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # 逗号分隔
    keywords: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 自动提取的关键词

    # 关系
    video: Mapped[Optional["Video"]] = relationship("Video", backref="notes")
    timestamps: Mapped[List["NoteTimestamp"]] = relationship(
        "NoteTimestamp", back_populates="note", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        """将笔记转换为字典"""
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "note_type": self.note_type,
            "video_id": self.video_id,
            "video_title": self.video.title if self.video else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "tags": self.tags.split(",") if self.tags else [],
            "keywords": self.keywords.split(",") if self.keywords else [],
            "timestamps": [ts.to_dict() for ts in self.timestamps] if self.timestamps else [],
        }


class NoteTimestamp(Base):
    """笔记时间戳模型，用于关联笔记与视频的特定时间点"""

    __tablename__ = "note_timestamps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_id: Mapped[int] = mapped_column(Integer, ForeignKey("notes.id"), nullable=False)
    time_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    subtitle_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 关系
    note: Mapped["Note"] = relationship("Note", back_populates="timestamps")

    def to_dict(self) -> dict:
        """将时间戳转换为字典"""
        return {
            "id": self.id,
            "note_id": self.note_id,
            "time_seconds": self.time_seconds,
            "subtitle_text": self.subtitle_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
