"""
相似度审计日志与可观测性测试
- 审计日志记录
- 指标统计
- 漂移检测
"""

from datetime import datetime

from app.services.similarity_analytics import SimilarityAuditLog
from app.services.similarity_analytics import SimilarityAuditLogger
from app.services.similarity_analytics import SimilarityEventType
from app.services.similarity_analytics import SimilarityMetrics


class TestSimilarityAuditLog:
    """审计日志记录测试"""

    def test_audit_log_creation(self):
        """测试创建审计日志"""
        log = SimilarityAuditLog(
            tag1="Python",
            tag2="编程",
            model="qwen-max",
            provider="openai",
            prompt_version="v2",
        )
        assert log.tag1 == "Python"
        assert log.tag2 == "编程"
        assert log.model == "qwen-max"

    def test_audit_log_has_trace_id(self):
        """测试审计日志自动生成 trace_id"""
        log = SimilarityAuditLog()
        assert log.trace_id is not None
        assert len(log.trace_id) > 0

    def test_audit_log_has_timestamp(self):
        """测试审计日志自动生成时间戳"""
        log = SimilarityAuditLog()
        assert log.timestamp is not None
        # 应该是 ISO 格式
        assert "T" in log.timestamp or ":" in log.timestamp

    def test_audit_log_success_fields(self):
        """测试成功日志的字段"""
        log = SimilarityAuditLog(
            success=True,
            score=0.85,
            parse_ok=True,
            latency_ms=100.5,
        )
        assert log.success is True
        assert log.score == 0.85
        assert log.parse_ok is True

    def test_audit_log_error_fields(self):
        """测试错误日志的字段"""
        log = SimilarityAuditLog(
            success=False,
            parse_ok=False,
            parse_error_type="parse_error_no_number",
            error_message="无法提取数字",
        )
        assert log.success is False
        assert log.parse_error_type == "parse_error_no_number"

    def test_audit_log_retry_fields(self):
        """测试重试日志的字段"""
        log = SimilarityAuditLog(
            retry_count=1,
            retry_failed=False,
        )
        assert log.retry_count == 1

    def test_audit_log_to_dict(self):
        """测试审计日志转字典"""
        log = SimilarityAuditLog(tag1="A", tag2="B")
        d = log.to_dict()
        assert isinstance(d, dict)
        assert d["tag1"] == "A"
        assert d["tag2"] == "B"

    def test_audit_log_to_json(self):
        """测试审计日志转 JSON"""
        log = SimilarityAuditLog(tag1="A", tag2="B", score=0.75)
        json_str = log.to_json()
        assert isinstance(json_str, str)
        assert "A" in json_str
        assert "0.75" in json_str

    def test_audit_log_event_types(self):
        """测试审计日志事件类型"""
        log_start = SimilarityAuditLog(event_type=SimilarityEventType.CALL_START.value)
        assert log_start.event_type == "similarity_call_start"

        log_success = SimilarityAuditLog(event_type=SimilarityEventType.CALL_SUCCESS.value)
        assert log_success.event_type == "similarity_call_success"


class TestSimilarityAuditLogger:
    """审计日志记录器测试"""

    def test_logger_creation(self):
        """测试创建审计日志记录器"""
        logger = SimilarityAuditLogger()
        assert logger.logger is not None

    def test_log_call_start(self):
        """测试记录调用开始"""
        logger = SimilarityAuditLogger()
        log = logger.log_call_start(
            trace_id="trace123",
            tag1="Python",
            tag2="编程",
            prompt_version="v2",
            provider="openai",
            model="qwen-max",
        )
        assert log.tag1 == "Python"
        assert log.event_type == SimilarityEventType.CALL_START.value

    def test_log_success(self):
        """测试记录成功"""
        logger = SimilarityAuditLogger()
        log = SimilarityAuditLog(tag1="A", tag2="B")

        logger.log_success(
            log,
            score=0.85,
            score_raw="0.85",
            provider_latency_ms=10.0,
            parse_latency_ms=2.0,
        )

        assert log.success is True
        assert log.score == 0.85
        assert log.parse_ok is True
        assert log.latency_ms == 12.0  # 总耗时

    def test_log_parse_error(self):
        """测试记录解析错误"""
        logger = SimilarityAuditLogger()
        log = SimilarityAuditLog(tag1="A", tag2="B")

        logger.log_parse_error(
            log,
            error_type="parse_error_no_number",
            error_message="No number",
            score_raw=None,
            parse_latency_ms=1.0,
        )

        assert log.success is False
        assert log.parse_error_type == "parse_error_no_number"

    def test_log_provider_error(self):
        """测试记录提供商错误"""
        logger = SimilarityAuditLogger()
        log = SimilarityAuditLog(tag1="A", tag2="B")

        logger.log_provider_error(
            log,
            error_type="provider_error_timeout",
            error_message="Timeout",
            latency_ms=15000.0,
        )

        assert log.success is False
        assert log.error_message == "Timeout"

    def test_log_retry(self):
        """测试记录重试"""
        logger = SimilarityAuditLogger()
        log = SimilarityAuditLog(tag1="A", tag2="B")

        logger.log_retry(log, attempt=1, reason="timeout")

        assert log.retry_count == 1
        assert log.metadata.get("retry_reason") == "timeout"

    def test_log_fallback(self):
        """测试记录降级"""
        logger = SimilarityAuditLogger()
        log = SimilarityAuditLog(tag1="A", tag2="B")

        logger.log_fallback(
            log,
            fallback_reason="parser_failed",
            fallback_score=0.5,
        )

        assert log.fallback_reason == "parser_failed"
        assert log.score == 0.5


class TestSimilarityMetrics:
    """性能指标收集器测试"""

    def test_metrics_creation(self):
        """测试创建指标收集器"""
        metrics = SimilarityMetrics()
        assert metrics.logs == []

    def test_record_log(self):
        """测试记录日志"""
        metrics = SimilarityMetrics()
        log = SimilarityAuditLog(tag1="A", tag2="B", score=0.75)

        metrics.record_log(log)

        assert len(metrics.logs) == 1
        assert metrics.logs[0].score == 0.75

    def test_get_stats_for_day_empty(self):
        """测试空集合的统计"""
        metrics = SimilarityMetrics()
        stats = metrics.get_stats_for_day("2024-01-01")

        assert stats["total_calls"] == 0

    def test_get_stats_for_day_single_success(self):
        """测试单条成功日志的统计"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow().isoformat()

        log = SimilarityAuditLog(
            timestamp=now,
            success=True,
            score=0.85,
            latency_ms=10.0,
        )
        metrics.record_log(log)

        today = now[:10]  # ISO 日期
        stats = metrics.get_stats_for_day(today)

        assert stats["total_calls"] == 1
        assert stats["success_count"] == 1
        assert stats["success_rate"] == 1.0
        assert stats["avg_score"] == 0.85

    def test_get_stats_for_day_multiple_logs(self):
        """测试多条日志的统计"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow()

        # 创建 3 条成功日志和 1 条失败日志
        for i in range(3):
            log = SimilarityAuditLog(
                timestamp=now.isoformat(),
                success=True,
                score=0.8,
                latency_ms=10.0 + i,
            )
            metrics.record_log(log)

        fail_log = SimilarityAuditLog(
            timestamp=now.isoformat(),
            success=False,
            latency_ms=5.0,
        )
        metrics.record_log(fail_log)

        today = now.date().isoformat()
        stats = metrics.get_stats_for_day(today)

        assert stats["total_calls"] == 4
        assert stats["success_count"] == 3
        assert stats["success_rate"] == 0.75

    def test_get_stats_parse_error_rate(self):
        """测试解析错误率统计"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow()

        # 2 条成功，1 条解析错误
        for _ in range(2):
            log = SimilarityAuditLog(
                timestamp=now.isoformat(),
                success=True,
                parse_ok=True,
                score=0.8,
            )
            metrics.record_log(log)

        error_log = SimilarityAuditLog(
            timestamp=now.isoformat(),
            success=False,
            parse_ok=False,
        )
        metrics.record_log(error_log)

        today = now.date().isoformat()
        stats = metrics.get_stats_for_day(today)

        assert stats["parse_error_rate"] == 1 / 3

    def test_get_stats_retry_rate(self):
        """测试重试率统计"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow()

        # 1 条重试，2 条无重试
        retry_log = SimilarityAuditLog(
            timestamp=now.isoformat(),
            retry_count=1,
        )
        metrics.record_log(retry_log)

        for _ in range(2):
            log = SimilarityAuditLog(
                timestamp=now.isoformat(),
                retry_count=0,
            )
            metrics.record_log(log)

        today = now.date().isoformat()
        stats = metrics.get_stats_for_day(today)

        assert stats["retry_rate"] == 1 / 3

    def test_check_drift_no_drift(self):
        """测试无漂移检测"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow()

        # 记录 3 条均值 0.5 ± 0.02 的日志
        for score in [0.48, 0.50, 0.52]:
            log = SimilarityAuditLog(
                timestamp=now.isoformat(),
                success=True,
                score=score,
            )
            metrics.record_log(log)

        today = now.date().isoformat()
        drift = metrics.check_drift(
            target_date=today,
            baseline_mean=0.5,
            threshold=0.1,
        )

        assert drift["drift_detected"] is False

    def test_check_drift_detected(self):
        """测试检测到漂移"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow()

        # 记录 3 条均值 0.8 的日志（与基线 0.5 偏离 60%）
        for score in [0.78, 0.80, 0.82]:
            log = SimilarityAuditLog(
                timestamp=now.isoformat(),
                success=True,
                score=score,
            )
            metrics.record_log(log)

        today = now.date().isoformat()
        drift = metrics.check_drift(
            target_date=today,
            baseline_mean=0.5,
            threshold=0.1,  # 10% 阈值
        )

        assert drift["drift_detected"] is True
        assert drift["drift_pct"] > 0.1

    def test_check_drift_boundary(self):
        """测试漂移边界情况"""
        metrics = SimilarityMetrics()
        now = datetime.utcnow()

        # 均值恰好在阈值边界
        log = SimilarityAuditLog(
            timestamp=now.isoformat(),
            success=True,
            score=0.55,  # 0.5 + 0.05 = 10% 偏移
        )
        metrics.record_log(log)

        today = now.date().isoformat()
        drift = metrics.check_drift(
            target_date=today,
            baseline_mean=0.5,
            threshold=0.1,
        )

        # 应该不触发告警（因为 <= 阈值）
        assert drift["drift_detected"] is False
