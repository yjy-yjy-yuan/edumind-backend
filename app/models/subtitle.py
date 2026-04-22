"""字幕模型 - SQLAlchemy 2.0 版本"""

from datetime import datetime
from typing import TYPE_CHECKING
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


class Subtitle(Base):
    """字幕模型"""

    __tablename__ = "subtitles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id"), nullable=False)

    # 字幕时间信息
    start_time: Mapped[float] = mapped_column(Float, nullable=False)  # 开始时间（秒）
    end_time: Mapped[float] = mapped_column(Float, nullable=False)  # 结束时间（秒）

    # 字幕内容
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # 字幕来源和类型
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # 'asr', 'extract', 'manual'
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="zh")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    video: Mapped["Video"] = relationship("Video", back_populates="subtitles")

    def __repr__(self) -> str:
        return f"<Subtitle {self.id} for Video {self.video_id}>"

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
            "source": self.source,
            "language": self.language,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def format_time(seconds: float) -> str:
        """将秒数转换为 MM:SS 格式"""
        minutes = int(float(seconds) // 60)
        secs = int(float(seconds) % 60)
        return f"{minutes:02d}:{secs:02d}"

    def to_srt(self, index: int) -> str:
        """将字幕转换为 SRT 格式"""
        start_mm = int(float(self.start_time) // 60)
        start_ss = int(float(self.start_time) % 60)
        end_mm = int(float(self.end_time) // 60)
        end_ss = int(float(self.end_time) % 60)
        return f"{index}\n{start_mm:02d}:{start_ss:02d} - {end_mm:02d}:{end_ss:02d}\n{self.text}\n"
