"""推荐运营事件落库模型（支撑运营聚合 API 的持久化口径）。"""

from datetime import datetime
from typing import Any
from typing import Optional

from app.models.base import Base
from sqlalchemy import JSON
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class RecommendationOpsEvent(Base):
    """推荐域运营事件（轻量事件日志）。"""

    __tablename__ = "recommendation_ops_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trace_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    def __repr__(self) -> str:
        return f"<RecommendationOpsEvent type={self.event_type} status={self.status}>"
