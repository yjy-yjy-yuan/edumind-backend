"""
相似度计算的结构化日志与审计追踪
- 审计字段：trace_id、prompt_version、model、path(ollama/openai)、latency_ms、parse_ok、score_raw、score_norm
- 错误分型：timeout、provider_error、parse_error、policy_blocked
- 效果监控：按天统计均值、方差、失败率、重试率、P95 延迟
"""

import json
import logging
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum
from typing import Any
from typing import Dict
from typing import Literal
from typing import Optional


class SimilarityEventType(Enum):
    """相似度计算事件类型"""

    CALL_START = "similarity_call_start"
    CALL_SUCCESS = "similarity_call_success"
    CALL_FAILED = "similarity_call_failed"
    RETRY_START = "similarity_retry_start"
    FALLBACK = "similarity_fallback"


class SimilarityProvider(Enum):
    """相似度提供商"""

    OPENAI = "openai"
    OLLAMA = "ollama"
    FALLBACK = "fallback"


@dataclass
class SimilarityAuditLog:
    """
    相似度计算的审计日志记录

    用途：
    - 结构化记录每次相似度计算的详细信息
    - 支持事后分析、性能监控、问题诊断
    - 作为数据反馈闭环的基础
    """

    # 基础字段
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    event_type: str = SimilarityEventType.CALL_START.value

    # 输入字段
    tag1: str = ""
    tag2: str = ""
    prompt_version: str = "v2"

    # 执行环境
    provider: str = SimilarityProvider.OPENAI.value
    model: str = ""

    # 执行结果
    success: bool = False
    score: Optional[float] = None  # 最终分值
    score_raw: Optional[str] = None  # 原始提取字符串
    score_normalized: Optional[float] = None  # 正规化后的分值

    # 性能指标
    latency_ms: float = 0.0  # 总耗时（毫秒）
    provider_latency_ms: float = 0.0  # 提供商调用耗时
    parse_latency_ms: float = 0.0  # 解析耗时

    # 解析信息
    parse_ok: bool = False
    parse_error_type: Optional[str] = None  # ParseErrorType 值

    # 重试信息
    retry_count: int = 0
    retry_failed: bool = False

    # 降级信息
    fallback_reason: Optional[str] = None

    # 其他
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)  # 扩展字段

    def to_dict(self) -> Dict[str, Any]:
        """转为字典（便于 JSON 序列化）"""
        return asdict(self)

    def to_json(self) -> str:
        """转为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class SimilarityAuditLogger:
    """
    相似度审计日志记录器

    用途：
    - 集中式记录相似度计算的各个阶段
    - 支持结构化输出便于后续分析
    - 统一写入 app.analytics 管道（向后兼容：方法签名与返回值不变）
    """

    def __init__(self, logger_name: str = "similarity_audit"):
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)

    def _emit_unified(self, log: SimilarityAuditLog) -> None:
        try:
            from app.analytics.adapters.similarity import emit_similarity_audit_event
            from app.analytics.pipeline import get_telemetry

            emit_similarity_audit_event(get_telemetry(), log)
        except Exception as exc:
            self.logger.debug("analytics pipeline emit skipped: %s", exc, exc_info=True)

    def log_call_start(
        self,
        trace_id: str,
        tag1: str,
        tag2: str,
        prompt_version: str,
        provider: str,
        model: str,
    ) -> SimilarityAuditLog:
        """记录计算开始"""
        log = SimilarityAuditLog(
            trace_id=trace_id,
            event_type=SimilarityEventType.CALL_START.value,
            tag1=tag1,
            tag2=tag2,
            prompt_version=prompt_version,
            provider=provider,
            model=model,
        )
        self._emit_unified(log)
        return log

    def log_success(
        self,
        log: SimilarityAuditLog,
        score: float,
        score_raw: str,
        provider_latency_ms: float,
        parse_latency_ms: float,
    ) -> None:
        """记录成功结果"""
        log.event_type = SimilarityEventType.CALL_SUCCESS.value
        log.success = True
        log.score = score
        log.score_raw = score_raw
        log.score_normalized = score
        log.provider_latency_ms = provider_latency_ms
        log.parse_latency_ms = parse_latency_ms
        log.parse_ok = True
        log.latency_ms = provider_latency_ms + parse_latency_ms
        self._emit_unified(log)

    def log_parse_error(
        self,
        log: SimilarityAuditLog,
        error_type: str,
        error_message: str,
        score_raw: Optional[str],
        parse_latency_ms: float,
    ) -> None:
        """记录解析错误"""
        log.event_type = SimilarityEventType.CALL_FAILED.value
        log.success = False
        log.parse_ok = False
        log.parse_error_type = error_type
        log.error_message = error_message
        log.score_raw = score_raw
        log.parse_latency_ms = parse_latency_ms
        self._emit_unified(log)

    def log_provider_error(
        self,
        log: SimilarityAuditLog,
        error_type: str,
        error_message: str,
        latency_ms: float,
    ) -> None:
        """记录提供商错误"""
        log.event_type = SimilarityEventType.CALL_FAILED.value
        log.success = False
        log.error_message = error_message
        log.provider_latency_ms = latency_ms
        log.latency_ms = latency_ms
        self._emit_unified(log)

    def log_retry(
        self,
        log: SimilarityAuditLog,
        attempt: int,
        reason: str,
    ) -> None:
        """记录重试"""
        log.event_type = SimilarityEventType.RETRY_START.value
        log.retry_count = attempt
        log.metadata['retry_reason'] = reason
        self._emit_unified(log)

    def log_fallback(
        self,
        log: SimilarityAuditLog,
        fallback_reason: str,
        fallback_score: Optional[float],
    ) -> None:
        """记录降级"""
        log.event_type = SimilarityEventType.FALLBACK.value
        log.fallback_reason = fallback_reason
        log.score = fallback_score
        self._emit_unified(log)


# ============================================================================
# 监控与分析（后续可接入 Prometheus、DataDog 等）
# ============================================================================
class SimilarityMetrics:
    """
    相似度计算的性能指标收集器

    用途：
    - 按天统计：均值、方差、失败率、重试率、P95 延迟
    - 漂移告警：检测分值分布异常
    """

    def __init__(self):
        self.logs: list = []

    def record_log(self, log: SimilarityAuditLog) -> None:
        """记录一条审计日志"""
        self.logs.append(log)

    def get_stats_for_day(self, target_date: str = None) -> Dict[str, Any]:
        """
        获取指定日期的统计信息

        Args:
            target_date: 目标日期（ISO 格式），如果为 None 则使用今天

        Returns:
            统计信息字典（schema 始终稳定）
        """
        if target_date is None:
            target_date = datetime.utcnow().date().isoformat()

        # 筛选当天日志
        day_logs = [log for log in self.logs if log.timestamp.startswith(target_date)]

        # 空集合时返回默认值
        if not day_logs:
            return {
                "date": target_date,
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

        # 计算统计
        scores = [log.score for log in day_logs if log.score is not None]
        latencies = [log.latency_ms for log in day_logs if log.latency_ms > 0]

        success_count = sum(1 for log in day_logs if log.success)
        retry_count = sum(1 for log in day_logs if log.retry_count > 0)
        parse_error_count = sum(1 for log in day_logs if not log.parse_ok)

        # 计算方差、均值、P95
        import statistics

        avg_score = statistics.mean(scores) if scores else None
        var_score = statistics.variance(scores) if len(scores) > 1 else None

        avg_latency = statistics.mean(latencies) if latencies else None
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else None

        return {
            "date": target_date,
            "total_calls": len(day_logs),
            "success_count": success_count,
            "success_rate": success_count / len(day_logs) if day_logs else 0,
            "retry_count": retry_count,
            "retry_rate": retry_count / len(day_logs) if day_logs else 0,
            "parse_error_count": parse_error_count,
            "parse_error_rate": parse_error_count / len(day_logs) if day_logs else 0,
            "avg_score": avg_score,
            "var_score": var_score,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
        }

    def check_drift(
        self,
        target_date: str = None,
        baseline_mean: float = 0.5,
        threshold: float = 0.1,
    ) -> Dict[str, Any]:
        """
        检测分值分布漂移（告警用）

        Args:
            target_date: 目标日期
            baseline_mean: 基线均值
            threshold: 告警阈值（例如 0.1 表示均值偏离基线 > 10%）

        Returns:
            包含漂移判断的字典
        """
        stats = self.get_stats_for_day(target_date)

        avg_score = stats.get("avg_score")
        if avg_score is None:
            return {"date": target_date, "drift_detected": False, "reason": "No valid scores"}

        drift_abs = abs(avg_score - baseline_mean)
        drift_pct = drift_abs / baseline_mean if baseline_mean != 0 else 0

        # 使用 epsilon 比较以处理浮点精度问题
        EPSILON = 1e-9
        drift_detected = (drift_pct - threshold) > EPSILON

        return {
            "date": target_date,
            "drift_detected": drift_detected,
            "baseline_mean": baseline_mean,
            "actual_mean": avg_score,
            "drift_abs": drift_abs,
            "drift_pct": drift_pct,
            "threshold": threshold,
        }
