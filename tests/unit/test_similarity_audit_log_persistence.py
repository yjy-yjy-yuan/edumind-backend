"""
相似度审计日志持久化 - TDD 单元测试

测试顺序：
1. Repository 层（数据库 CRUD）
2. Service 层（内存 + 同步持久化协调）
3. 查询聚合接口（按天统计、trace_id 溯源）
4. 降级与失败重试机制

依赖：
- SQLAlchemy Session（通过 fixture 提供）
"""

from datetime import datetime
from typing import Generator

import pytest
from app.models.base import Base
from app.models.similarity_audit_log import SimilarityAuditLogModel
from app.repositories.similarity_audit_log_repository import SimilarityAuditLogRepository
from app.services.similarity_analytics import SimilarityAuditLog
from app.services.similarity_analytics import SimilarityEventType
from app.services.similarity_audit_log_service import SimilarityAuditLogPersistenceService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

# ==================== Fixtures ====================


@pytest.fixture
def test_db() -> Generator[Session, None, None]:
    """创建测试数据库会话"""
    # 使用内存 SQLite 数据库以加速测试
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # 创建所有表
    Base.metadata.create_all(engine)

    # 创建会话工厂
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def sample_audit_log() -> SimilarityAuditLog:
    """创建样本审计日志"""
    log = SimilarityAuditLog(
        trace_id="trace_001",
        tag1="python",
        tag2="programming",
        prompt_version="v2",
        provider="openai",
        model="qwen-max",
        event_type=SimilarityEventType.CALL_SUCCESS.value,
    )
    log.success = True
    log.score = 0.87
    log.score_raw = "0.87"
    log.score_normalized = 0.87
    log.latency_ms = 150.5
    log.provider_latency_ms = 100.0
    log.parse_latency_ms = 50.5
    log.parse_ok = True
    log.retry_count = 0
    return log


# ==================== Repository 层测试 ====================


class TestSimilarityAuditLogRepository:
    """Repository 层单元测试"""

    def test_save_log_basic(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试保存单条日志"""
        repo = SimilarityAuditLogRepository()

        # 保存日志
        result = repo.save_log(sample_audit_log, test_db)

        # 验证返回的模型
        assert result is not None
        assert result.trace_id == "trace_001"
        assert result.tag1 == "python"
        assert result.tag2 == "programming"
        assert result.success is True
        assert result.score == 0.87

    def test_save_log_persistence(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试日志真正被持久化到数据库"""
        repo = SimilarityAuditLogRepository()

        # 保存日志
        repo.save_log(sample_audit_log, test_db)

        # 直接查询数据库验证
        db_log = test_db.query(SimilarityAuditLogModel).filter_by(trace_id="trace_001").first()

        assert db_log is not None
        assert db_log.score == 0.87
        assert db_log.latency_ms == 150.5

    def test_get_logs_by_date_empty(self, test_db: Session):
        """测试按日期查询空结果"""
        repo = SimilarityAuditLogRepository()

        logs = repo.get_logs_by_date("2026-04-10", test_db)

        assert logs == []

    def test_get_logs_by_date_with_results(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试按日期查询有结果"""
        repo = SimilarityAuditLogRepository()

        # 保存日志
        repo.save_log(sample_audit_log, test_db)

        # 查询当天日志
        today = datetime.utcnow().date().isoformat()
        logs = repo.get_logs_by_date(today, test_db)

        assert len(logs) == 1
        assert logs[0].trace_id == "trace_001"

    def test_get_log_by_trace_id(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试按 trace_id 查询"""
        repo = SimilarityAuditLogRepository()

        # 保存日志
        repo.save_log(sample_audit_log, test_db)

        # 按 trace_id 查询
        log = repo.get_log_by_trace_id("trace_001", test_db)

        assert log is not None
        assert log.tag1 == "python"
        assert log.score == 0.87

    def test_batch_save_logs(self, test_db: Session):
        """测试批量保存日志"""
        repo = SimilarityAuditLogRepository()

        # 创建多条日志
        logs = []
        for i in range(5):
            log = SimilarityAuditLog(
                trace_id=f"trace_{i:03d}",
                tag1=f"tag1_{i}",
                tag2=f"tag2_{i}",
                success=True,
                score=0.5 + i * 0.1,
            )
            logs.append(log)

        # 批量保存
        results = repo.batch_save_logs(logs, test_db)

        assert len(results) == 5

        # 验证数据库中的数据
        count = test_db.query(SimilarityAuditLogModel).count()
        assert count == 5


# ==================== Service 层测试 ====================


class TestSimilarityAuditLogPersistenceService:
    """Service 层单元测试"""

    def test_service_initialization(self):
        """测试 Service 初始化"""
        service = SimilarityAuditLogPersistenceService()

        assert service is not None
        # Service 应该有内存缓存和配置
        assert hasattr(service, "memory_logs")
        assert hasattr(service, "max_retries")

    def test_record_with_memory_and_persistence(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试记录日志同时进行内存和持久化"""
        service = SimilarityAuditLogPersistenceService()

        # 记录日志
        service.record_log(sample_audit_log, test_db)

        # 验证内存缓存
        assert len(service.memory_logs) == 1
        assert service.memory_logs[0].trace_id == "trace_001"

        # 验证数据库持久化
        db_log = test_db.query(SimilarityAuditLogModel).filter_by(trace_id="trace_001").first()
        assert db_log is not None

    def test_persistence_failure_does_not_block_memory(self, sample_audit_log: SimilarityAuditLog):
        """测试持久化失败不阻断内存记录"""
        service = SimilarityAuditLogPersistenceService()

        # 传入无效的 DB 会话，模拟持久化失败
        invalid_db = None

        # 应该不抛异常，而是降级到内存
        service.record_log(sample_audit_log, invalid_db)

        # 内存中应该仍然有记录
        assert len(service.memory_logs) == 1

    def test_get_stats_from_db_not_memory(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试按日期统计优先使用 DB（支持重启后数据恢复）"""
        service = SimilarityAuditLogPersistenceService()

        # 记录日志到 DB
        service.record_log(sample_audit_log, test_db)

        # 获取当天统计
        today = datetime.utcnow().date().isoformat()
        stats = service.get_daily_stats(today, test_db)

        # 应该返回实际数据
        assert stats["date"] == today
        assert stats["total_calls"] == 1
        assert stats["success_count"] == 1
        assert stats["success_rate"] == 1.0

    def test_daily_stats_empty_respects_query_date(self, test_db: Session):
        """无数据时返回的 date 字段应与查询日期一致（非固定为今天）"""
        service = SimilarityAuditLogPersistenceService()
        past = "2020-01-01"
        stats = service.get_daily_stats(past, test_db)
        assert stats["date"] == past
        assert stats["total_calls"] == 0

    def test_trace_id_lookup(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试按 trace_id 溯源"""
        service = SimilarityAuditLogPersistenceService()

        # 记录日志
        service.record_log(sample_audit_log, test_db)

        # 按 trace_id 查询
        log = service.get_log_by_trace_id("trace_001", test_db)

        assert log is not None
        assert log.tag1 == "python"
        assert log.score == 0.87


# ==================== 降级与重试测试 ====================


class TestPersistenceFailoverAndRetry:
    """降级与重试机制测试"""

    def test_fallback_to_memory_on_db_error(self, sample_audit_log: SimilarityAuditLog):
        """测试 DB 错误时降级到内存"""
        service = SimilarityAuditLogPersistenceService()

        # 尝试在 None DB 中保存（模拟 DB 连接失败）
        service.record_log(sample_audit_log, None)

        # 内存中应该有记录
        assert len(service.memory_logs) >= 1

    def test_retry_persistence_on_init_failure(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试初始持久化失败时的重试机制"""
        service = SimilarityAuditLogPersistenceService(max_retries=2)

        # 第一次用无效 DB，记录应该降级到内存
        service.record_log(sample_audit_log, None)

        # 第二次用有效 DB，应该重新持久化
        service.persist_from_memory(test_db)

        # 数据库中应该有记录
        db_log = test_db.query(SimilarityAuditLogModel).filter_by(trace_id="trace_001").first()
        assert db_log is not None
        # 成功后应从内存缓冲移除，避免重复落库
        assert len(service.memory_logs) == 0

    def test_max_memory_buffer_size(self, sample_audit_log: SimilarityAuditLog):
        """测试内存缓冲区大小限制"""
        service = SimilarityAuditLogPersistenceService(max_memory_buffer=10)

        # 添加超过buffer大小的日志（都用None DB，降级到内存）
        for i in range(15):
            log = SimilarityAuditLog(
                trace_id=f"trace_{i:03d}",
                tag1=f"tag1_{i}",
                tag2=f"tag2_{i}",
            )
            service.record_log(log, None)

        # 内存中应该只保留最近的 10 条
        assert len(service.memory_logs) <= 10


# ==================== 集成测试 ====================


class TestSimilarityAuditLogIntegration:
    """集成测试"""

    def test_end_to_end_save_and_query(self, test_db: Session):
        """端到端测试：保存多条日志并查询"""
        service = SimilarityAuditLogPersistenceService()

        # 模拟多次计算
        for i in range(3):
            log = SimilarityAuditLog(
                trace_id=f"trace_{i:03d}",
                tag1="python",
                tag2="java",
                success=True,
                score=0.7 + i * 0.1,
            )
            log.latency_ms = 100.0 + i * 50
            service.record_log(log, test_db)

        # 按日期查询
        today = datetime.utcnow().date().isoformat()
        stats = service.get_daily_stats(today, test_db)

        # 验证统计结果
        assert stats["total_calls"] == 3
        assert stats["success_count"] == 3
        assert stats["avg_score"] is not None
        assert stats["avg_score"] > 0.7

    def test_restart_recovery(self, test_db: Session, sample_audit_log: SimilarityAuditLog):
        """测试重启后日志不丢失"""
        # 第一个 Service 实例写入日志
        service1 = SimilarityAuditLogPersistenceService()
        service1.record_log(sample_audit_log, test_db)

        # 创建新的 Service 实例（模拟重启）
        service2 = SimilarityAuditLogPersistenceService()

        # 从 DB 查询日志
        log = service2.get_log_by_trace_id("trace_001", test_db)

        # 应该恢复出来
        assert log is not None
        assert log.score == 0.87
