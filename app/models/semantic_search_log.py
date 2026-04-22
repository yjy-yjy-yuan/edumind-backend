"""语义搜索查询日志（跨视频全局检索落库）"""

from datetime import datetime
from decimal import Decimal
from typing import List
from typing import Optional

from app.models.base import Base
from sqlalchemy import JSON
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class SemanticSearchLog(Base):
    """
    记录用户发起的跨视频（全局）语义搜索，便于统计与审计。

    仅当请求未携带 video_ids（或为空列表）时写入，与前端「全部已索引视频」范围一致。
    """

    __tablename__ = "semantic_search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    video_ids_searched: Mapped[Optional[List[int]]] = mapped_column(JSON, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_used: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold_used: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    def __repr__(self) -> str:
        return f"<SemanticSearchLog user={self.user_id} global={self.is_global} results={self.result_count}>"
