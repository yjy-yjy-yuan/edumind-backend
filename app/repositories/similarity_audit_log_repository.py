"""Repository 层 - 数据库访问对象"""

from datetime import datetime
from typing import List
from typing import Optional

from app.models.similarity_audit_log import SimilarityAuditLogModel
from app.services.similarity_analytics import SimilarityAuditLog
from sqlalchemy import desc
from sqlalchemy.orm import Session


class SimilarityAuditLogRepository:
    """相似度审计日志持久化仓库"""

    def save_log(
        self,
        audit_log: SimilarityAuditLog,
        db: Session,
    ) -> SimilarityAuditLogModel:
        """
        保存单条审计日志到数据库

        Args:
            audit_log: 内存中的审计日志对象
            db: SQLAlchemy 会话

        Returns:
            持久化后的 ORM 模型对象

        Raises:
            可能抛出 SQLAlchemy 异常（由调用者处理降级）
        """
        # 将内存对象转换为 ORM 模型
        db_log = SimilarityAuditLogModel.from_audit_log(audit_log)

        # 保存到数据库
        db.add(db_log)
        db.commit()
        db.refresh(db_log)

        return db_log

    def batch_save_logs(
        self,
        audit_logs: List[SimilarityAuditLog],
        db: Session,
    ) -> List[SimilarityAuditLogModel]:
        """
        批量保存审计日志到数据库（高效批处理）

        Args:
            audit_logs: 审计日志列表
            db: SQLAlchemy 会话

        Returns:
            持久化后的 ORM 模型列表
        """
        if not audit_logs:
            return []

        # 转换为 ORM 模型
        db_logs = [SimilarityAuditLogModel.from_audit_log(log) for log in audit_logs]

        # 批量插入
        db.add_all(db_logs)
        db.commit()

        # 刷新以获取 ID
        for db_log in db_logs:
            db.refresh(db_log)

        return db_logs

    def get_logs_by_date(
        self,
        date_key: str,
        db: Session,
    ) -> List[SimilarityAuditLogModel]:
        """
        按日期查询审计日志（优化了索引）

        Args:
            date_key: 日期键（ISO 格式 YYYY-MM-DD）
            db: SQLAlchemy 会话

        Returns:
            该日期的所有审计日志列表
        """
        logs = (
            db.query(SimilarityAuditLogModel)
            .filter(SimilarityAuditLogModel.date_key == date_key)
            .order_by(desc(SimilarityAuditLogModel.created_at))
            .all()
        )
        return logs

    def get_log_by_trace_id(
        self,
        trace_id: str,
        db: Session,
    ) -> Optional[SimilarityAuditLogModel]:
        """
        按 trace_id 查询单条审计日志

        Args:
            trace_id: 追踪 ID
            db: SQLAlchemy 会话

        Returns:
            匹配的审计日志，若不存在则返回 None
        """
        log = db.query(SimilarityAuditLogModel).filter(SimilarityAuditLogModel.trace_id == trace_id).first()
        return log

    def get_logs_in_date_range(
        self,
        start_date: str,
        end_date: str,
        db: Session,
    ) -> List[SimilarityAuditLogModel]:
        """
        按日期范围查询审计日志

        Args:
            start_date: 开始日期（ISO 格式）
            end_date: 结束日期（ISO 格式）
            db: SQLAlchemy 会话

        Returns:
            日期范围内的所有审计日志列表
        """
        logs = (
            db.query(SimilarityAuditLogModel)
            .filter(
                SimilarityAuditLogModel.date_key >= start_date,
                SimilarityAuditLogModel.date_key <= end_date,
            )
            .order_by(SimilarityAuditLogModel.date_key.desc())
            .all()
        )
        return logs

    def delete_logs_before_date(
        self,
        before_date: str,
        db: Session,
    ) -> int:
        """
        删除指定日期之前的审计日志（数据清理用）

        Args:
            before_date: 删除此日期之前的日志（ISO 格式）
            db: SQLAlchemy 会话

        Returns:
            删除的行数
        """
        result = db.query(SimilarityAuditLogModel).filter(SimilarityAuditLogModel.date_key < before_date).delete()
        db.commit()
        return result
