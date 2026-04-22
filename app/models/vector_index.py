"""向量索引模型 - SQLAlchemy 2.0 版本"""

from datetime import datetime
from enum import Enum
from typing import Optional

from app.models.base import Base
from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class VectorIndexStatus(str, Enum):
    """向量索引状态枚举"""

    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"  # 完成
    FAILED = "failed"  # 失败


class VectorIndex(Base):
    """向量索引模型 - 记录视频的语义索引状态和元数据"""

    __tablename__ = "vector_indexes"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 外键
    video_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # ChromaDB 集合信息
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # 索引详情
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_backend: Mapped[str] = mapped_column(String(50), default="gemini")
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 状态管理
    status: Mapped[VectorIndexStatus] = mapped_column(
        SQLEnum(VectorIndexStatus, values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        default=VectorIndexStatus.PENDING,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<VectorIndex video={self.video_id} status={self.status}>"
