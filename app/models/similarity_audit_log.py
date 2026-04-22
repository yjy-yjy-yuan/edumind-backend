"""相似度审计日志持久化模型 - ORM 定义"""

from datetime import datetime
from typing import Optional

from app.models.base import Base
from sqlalchemy import JSON
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class SimilarityAuditLogModel(Base):
    """
    相似度计算的审计日志持久化模型

    用途：
    - 在数据库中持久化每次相似度计算的详细信息
    - 支持重启不丢失日志
    - 支持按天统计和按 trace_id 回溯
    - 基于现有 SimilarityAuditLog 数据类结构

    索引策略：
    - (date_key, trace_id)：快速按天和 trace_id 查询
    - trace_id：快速按 trace_id 查询
    - created_at：按创建时间查询
    """

    __tablename__ = "similarity_audit_logs"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 追踪 ID（关键字段，便于回溯）
    trace_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # 日期键（优化按天查询）
    date_key: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # 输入字段
    tag1: Mapped[str] = mapped_column(String(255), nullable=False)
    tag2: Mapped[str] = mapped_column(String(255), nullable=False)

    # 事件与环境
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v2")

    # 执行结果
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score_normalized: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 性能指标（单位：毫秒）
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    provider_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    parse_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # 解析信息
    parse_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parse_error_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 重试信息
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 降级信息
    fallback_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 错误信息
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 扩展字段（JSON，重命名为 extra_metadata 避免与 SQLAlchemy metadata 冲突）
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<SimilarityAuditLogModel trace_id={self.trace_id} "
            f"tags=({self.tag1},{self.tag2}) success={self.success}>"
        )

    @staticmethod
    def from_audit_log(audit_log) -> "SimilarityAuditLogModel":
        """从内存 SimilarityAuditLog 数据类转换为 ORM 模型"""
        from datetime import datetime

        # 提取日期（ISO 格式）
        timestamp_str = audit_log.timestamp
        date_key = timestamp_str[:10] if timestamp_str else datetime.utcnow().date().isoformat()

        return SimilarityAuditLogModel(
            trace_id=audit_log.trace_id,
            date_key=date_key,
            tag1=audit_log.tag1,
            tag2=audit_log.tag2,
            event_type=audit_log.event_type,
            provider=audit_log.provider,
            model=audit_log.model,
            prompt_version=audit_log.prompt_version,
            success=audit_log.success,
            score=audit_log.score,
            score_raw=audit_log.score_raw,
            score_normalized=audit_log.score_normalized,
            latency_ms=audit_log.latency_ms,
            provider_latency_ms=audit_log.provider_latency_ms,
            parse_latency_ms=audit_log.parse_latency_ms,
            parse_ok=audit_log.parse_ok,
            parse_error_type=audit_log.parse_error_type,
            retry_count=audit_log.retry_count,
            retry_failed=audit_log.retry_failed,
            fallback_reason=audit_log.fallback_reason,
            error_message=audit_log.error_message,
            extra_metadata=audit_log.metadata,
        )
