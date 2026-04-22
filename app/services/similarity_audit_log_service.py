"""Service 层 - 相似度审计日志持久化服务

负责协调内存缓存和数据库持久化，并提供降级与重试机制。
"""

import logging
import time
from collections import deque
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from app.repositories.similarity_audit_log_repository import SimilarityAuditLogRepository
from app.services.similarity_analytics import SimilarityAuditLog
from app.services.similarity_analytics import SimilarityMetrics
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class SimilarityAuditLogPersistenceService:
    """
    相似度审计日志持久化服务

    职责：
    1. 内存缓存管理（向下兼容 SimilarityMetrics）
    2. 同步持久化到数据库（DB 失败不阻断主流程；调用方短会话写入）
    3. 失败重试与降级策略（内存缓冲）
    4. 查询接口（按天统计、trace_id 溯源）
    5. 服务重启后数据恢复

    设计原则：
    - 可用性优先：DB 故障不影响主流程
    - 查询时优先读 DB，失败再读内存缓冲
    - 内存安全：缓冲区有大小限制
    """

    def __init__(
        self,
        max_memory_buffer: int = 1000,
        max_retries: int = 3,
        logger_name: str = "similarity_audit_persistence",
    ):
        """
        初始化服务

        Args:
            max_memory_buffer: 内存缓冲区最大日志数
            max_retries: 持久化重试次数
            logger_name: 日志记录器名称
        """
        self.max_memory_buffer = max_memory_buffer
        self.max_retries = max_retries

        # 内存缓冲区（FIFO 队列）
        self.memory_logs: deque = deque(maxlen=max_memory_buffer)

        # 仓库实例
        self.repository = SimilarityAuditLogRepository()

        # 兼容原有 SimilarityMetrics 的内存指标
        self.metrics = SimilarityMetrics()

        # 日志记录
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)

    def record_log(
        self,
        audit_log: SimilarityAuditLog,
        db: Optional[Session],
    ) -> bool:
        """
        记录审计日志（内存+持久化）

        流程：
        1. 添加到内存缓冲区
        2. 同步尝试持久化到 DB（失败则降级到内存）
        3. 更新内存指标

        Args:
            audit_log: 审计日志对象
            db: 数据库会话（可选，None 时仅使用内存）

        Returns:
            是否成功持久化到 DB（False 表示降级到内存）
        """
        # 1. 添加到内存缓冲
        self.memory_logs.append(audit_log)
        self.metrics.record_log(audit_log)

        # 2. 尝试持久化到 DB
        if db is not None:
            try:
                self.repository.save_log(audit_log, db)
                self.logger.info(
                    f"✅ Persisted log: trace_id={audit_log.trace_id}, " f"tag1={audit_log.tag1}, tag2={audit_log.tag2}"
                )
                return True
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                self.logger.warning(
                    f"⚠️ Failed to persist log (trace_id={audit_log.trace_id}): {str(e)}. " f"Falling back to memory."
                )
                return False
        else:
            self.logger.debug(f"No DB session provided, using memory only: trace_id={audit_log.trace_id}")
            return False

    def persist_from_memory(self, db: Session) -> int:
        """
        从内存缓冲区重新持久化（用于恢复 DB 连接后补回数据）

        成功落库的记录会从内存缓冲中移除，避免重复插入；重试间隔为指数退避（有上限）。

        Args:
            db: 数据库会话

        Returns:
            成功持久化的日志数量
        """
        if not self.memory_logs:
            return 0

        # 转列表以便迭代（deque 在迭代中修改不安全）
        logs_to_persist = list(self.memory_logs)
        persisted_count = 0

        for log in logs_to_persist:
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    self.repository.save_log(log, db)
                    persisted_count += 1
                    self.logger.info(f"✅ Recovered log from memory: trace_id={log.trace_id}")
                    try:
                        self.memory_logs.remove(log)
                    except ValueError:
                        pass
                    break
                except Exception as e:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    retry_count += 1
                    self.logger.warning(
                        f"⚠️ Retry {retry_count}/{self.max_retries} for trace_id={log.trace_id}: {str(e)}"
                    )
                    if retry_count < self.max_retries:
                        # 指数退避（秒），上限 2s
                        delay = min(0.05 * (2 ** (retry_count - 1)), 2.0)
                        time.sleep(delay)

        self.logger.info(f"Memory persistence recovered: {persisted_count}/{len(logs_to_persist)}")
        return persisted_count

    def get_daily_stats(
        self,
        date_key: Optional[str],
        db: Optional[Session],
    ) -> Dict[str, Any]:
        """
        按日期获取统计信息

        数据来源优先级：
        1. 若有 DB，优先从 DB 查询（支持重启恢复）
        2. 否则从内存中查询

        Args:
            date_key: 日期键（ISO 格式 YYYY-MM-DD），若 None 则使用今天
            db: 数据库会话（可选）

        Returns:
            统计信息字典
        """
        if date_key is None:
            date_key = datetime.utcnow().date().isoformat()

        # 优先从 DB 查询（保证重启后数据一致）
        if db is not None:
            try:
                db_logs = self.repository.get_logs_by_date(date_key, db)
                if db_logs:
                    return self._compute_stats(db_logs, date_key)
            except Exception as e:
                self.logger.warning(
                    f"Failed to query from DB for date {date_key}: {str(e)}. " f"Falling back to memory."
                )

        # 降级到内存查询
        memory_logs = [log for log in self.memory_logs if log.timestamp.startswith(date_key)]
        return self._compute_stats(memory_logs, date_key)

    def get_log_by_trace_id(
        self,
        trace_id: str,
        db: Optional[Session],
    ) -> Optional[SimilarityAuditLog]:
        """
        按 trace_id 查询单条日志（溯源用）

        数据来源优先级：
        1. 若有 DB，优先从 DB 查询
        2. 否则从内存中查询

        Args:
            trace_id: 追踪 ID
            db: 数据库会话（可选）

        Returns:
            审计日志对象或 None
        """
        # 优先从 DB 查询
        if db is not None:
            try:
                db_log = self.repository.get_log_by_trace_id(trace_id, db)
                if db_log is not None:
                    # 转换 ORM 模型回内存对象（简化，返回 ORM 对象也可）
                    return self._orm_to_audit_log(db_log)
            except Exception as e:
                self.logger.warning(
                    f"Failed to query DB for trace_id={trace_id}: {str(e)}. " f"Falling back to memory."
                )

        # 降级到内存查询
        for log in self.memory_logs:
            if log.trace_id == trace_id:
                return log

        return None

    def get_logs_in_date_range(
        self,
        start_date: str,
        end_date: str,
        db: Optional[Session],
    ) -> List[SimilarityAuditLog]:
        """
        按日期范围查询日志

        Args:
            start_date: 开始日期（ISO 格式）
            end_date: 结束日期（ISO 格式）
            db: 数据库会话（可选）

        Returns:
            日志列表
        """
        if db is not None:
            try:
                db_logs = self.repository.get_logs_in_date_range(start_date, end_date, db)
                return [self._orm_to_audit_log(log) for log in db_logs]
            except Exception as e:
                self.logger.warning(f"Failed to query date range from DB: {str(e)}")

        # 降级到内存
        memory_logs = [log for log in self.memory_logs if start_date <= log.timestamp[:10] <= end_date]
        return memory_logs

    # ==================== 私有工具方法 ====================

    @staticmethod
    def _compute_stats(logs: List, date_key: str) -> Dict[str, Any]:
        """
        计算统计信息（支持 ORM 模型和内存对象）

        Args:
            logs: 日志列表（SimilarityAuditLog 或 SimilarityAuditLogModel）
            date_key: 统计所归属的日期（YYYY-MM-DD）

        Returns:
            统计字典
        """
        import statistics

        if not logs:
            return {
                "date": date_key,
                "total_calls": 0,
                "success_count": 0,
                "success_rate": 0.0,
                "retry_count": 0,
                "retry_rate": 0.0,
                "parse_error_count": 0,
                "parse_error_rate": 0.0,
                "avg_score": None,
                "var_score": None,
                "avg_latency_ms": None,
                "p95_latency_ms": None,
            }

        # 提取数据（兼容 ORM 模型和内存对象）
        scores = [log.score for log in logs if hasattr(log, 'score') and log.score is not None]
        latencies = [log.latency_ms for log in logs if hasattr(log, 'latency_ms') and log.latency_ms > 0]

        success_count = sum(1 for log in logs if getattr(log, 'success', False))
        retry_count = sum(1 for log in logs if getattr(log, 'retry_count', 0) > 0)
        parse_error_count = sum(1 for log in logs if not getattr(log, 'parse_ok', False))

        # 计算指标
        avg_score = statistics.mean(scores) if scores else None
        var_score = statistics.variance(scores) if len(scores) > 1 else None

        avg_latency = statistics.mean(latencies) if latencies else None
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else None

        return {
            "date": date_key,
            "total_calls": len(logs),
            "success_count": success_count,
            "success_rate": success_count / len(logs) if logs else 0,
            "retry_count": retry_count,
            "retry_rate": retry_count / len(logs) if logs else 0,
            "parse_error_count": parse_error_count,
            "parse_error_rate": parse_error_count / len(logs) if logs else 0,
            "avg_score": avg_score,
            "var_score": var_score,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
        }

    @staticmethod
    def _orm_to_audit_log(orm_log) -> SimilarityAuditLog:
        """
        将 ORM 模型转换为内存 SimilarityAuditLog 对象

        Args:
            orm_log: SimilarityAuditLogModel ORM 实例

        Returns:
            SimilarityAuditLog 内存对象
        """
        log = SimilarityAuditLog(
            trace_id=orm_log.trace_id,
            timestamp=orm_log.created_at.isoformat() if orm_log.created_at else "",
            event_type=orm_log.event_type,
            tag1=orm_log.tag1,
            tag2=orm_log.tag2,
            prompt_version=orm_log.prompt_version,
            provider=orm_log.provider,
            model=orm_log.model or "",
            success=orm_log.success,
            score=orm_log.score,
            score_raw=orm_log.score_raw,
            score_normalized=orm_log.score_normalized,
            latency_ms=orm_log.latency_ms,
            provider_latency_ms=orm_log.provider_latency_ms,
            parse_latency_ms=orm_log.parse_latency_ms,
            parse_ok=orm_log.parse_ok,
            parse_error_type=orm_log.parse_error_type,
            retry_count=orm_log.retry_count,
            retry_failed=orm_log.retry_failed,
            fallback_reason=orm_log.fallback_reason,
            error_message=orm_log.error_message,
            metadata=orm_log.extra_metadata or {},
        )
        return log
