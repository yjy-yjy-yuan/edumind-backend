"""
LLM相似度服务单元测试

覆盖范围：
- 重试循环与配置注入
- 返回值元组解包
- 延迟拆分（provider_latency vs parse_latency）
- max_retries生效验证
"""

from unittest.mock import patch

import pytest
from app.core.config import settings
from app.services.config_model_params import SimilarityConfig
from app.services.llm_similarity_service import LLMSimilarityService


class TestLLMSimilarityServiceRetry:
    """测试重试机制"""

    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    def test_max_retries_parameter_effect(self, mock_metrics, mock_audit_logger):
        """验证max_retries参数真实生效"""
        service = LLMSimilarityService()
        service.use_ollama = True
        service.use_openai = False

        # 模拟Ollama返回值为None（失败）
        with patch.object(service, '_calculate_similarity_with_ollama', return_value=None) as mock_ollama:
            # max_retries=1时，只尝试1次
            result = service._calculate_with_retry(trace_id="test1", tag1="tag1", tag2="tag2", max_retries=1)
            # 应该触发降级并返回默认值
            assert mock_ollama.call_count == 1
            assert result == (SimilarityConfig.DEFAULT_FALLBACK_SCORE_ON_ERROR or 0.0)

    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    @patch('time.sleep')  # Mock sleep防止测试缓慢
    def test_retry_loop_with_fallback(self, mock_sleep, mock_metrics, mock_audit_logger):
        """验证重试循环在所有尝试失败后触发降级"""
        service = LLMSimilarityService()
        service.use_ollama = True
        service.use_openai = False

        # Ollama全部失败
        with patch.object(service, '_calculate_similarity_with_ollama', return_value=None) as mock_ollama:
            result = service._calculate_with_retry(trace_id="test2", tag1="math", tag2="algebra", max_retries=2)
            # 应该触发降级并返回default值，且触发一次退避
            assert mock_ollama.call_count == 2
            assert mock_sleep.call_count == 1
            assert result == (SimilarityConfig.DEFAULT_FALLBACK_SCORE_ON_ERROR or 0.0)


class TestLLMSimilarityServiceReturnValue:
    """测试返回值元组解包"""

    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    @patch('time.perf_counter')
    def test_tuple_unpacking_ollama_success(self, mock_perf_counter, mock_metrics, mock_audit_logger):
        """验证Ollama返回(score, parse_elapsed)元组被正确解包"""
        service = LLMSimilarityService()
        service.use_ollama = True
        service.use_openai = False

        # 模拟perf_counter返回值
        mock_perf_counter.side_effect = [100.0, 100.2]  # 200ms总耗时

        # Ollama返回(score, parse_elapsed)
        with patch.object(
            service, '_calculate_similarity_with_ollama', return_value=(0.85, 50.0)  # score=0.85, parse_elapsed=50ms
        ):
            result = service._calculate_with_retry(trace_id="test3", tag1="python", tag2="programming", max_retries=1)

            # 应该返回score（float），不是tuple
            assert isinstance(result, float)
            assert result == 0.85


class TestLLMSimilarityServiceLatencySplit:
    """测试延迟拆分"""

    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    @patch('time.perf_counter')
    def test_provider_latency_calculation(self, mock_perf_counter, mock_metrics, mock_audit_logger):
        """验证provider_latency = total - parse_latency"""
        service = LLMSimilarityService()
        service.use_ollama = True
        service.use_openai = False

        # 模拟时间：总耗时200ms
        mock_perf_counter.side_effect = [100.0, 100.2]  # 0.2s = 200ms

        # Ollama返回(score, parse_elapsed_50ms)
        with patch.object(service, '_calculate_similarity_with_ollama', return_value=(0.75, 50.0)):
            service._calculate_with_retry(trace_id="test4", tag1="ai", tag2="machine_learning", max_retries=1)

            # 验证audit_logger.log_success被调用时的参数
            assert mock_audit_logger.log_success.called
            call_args = mock_audit_logger.log_success.call_args

            provider_latency_ms = call_args.kwargs["provider_latency_ms"]
            parse_latency_ms = call_args.kwargs["parse_latency_ms"]

            # total=200ms, parse=50ms => provider=150ms
            assert parse_latency_ms == 50.0
            assert provider_latency_ms == pytest.approx(150.0, abs=1e-6)

    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    @patch('time.perf_counter')
    def test_provider_latency_lower_bound_protection(self, mock_perf_counter, mock_metrics, mock_audit_logger):
        """验证provider_latency = max(0.0, total - parse)保护"""
        service = LLMSimilarityService()
        service.use_ollama = True
        service.use_openai = False

        # 模拟时间精度抖动：total_elapsed < parse_elapsed
        mock_perf_counter.side_effect = [100.0, 100.01]  # 仅10ms

        # Ollama返回(score, parse_elapsed_50ms)
        with patch.object(
            service,
            '_calculate_similarity_with_ollama',
            return_value=(0.8, 50.0),  # parse_elapsed > total_elapsed = 负数
        ):
            service._calculate_with_retry(trace_id="test5", tag1="test", tag2="case", max_retries=1)

            call_args = mock_audit_logger.log_success.call_args
            provider_latency_ms = call_args.kwargs.get('provider_latency_ms') or 0

            # 验证provider_latency >= 0（下限保护）
            assert provider_latency_ms >= 0.0


class TestLLMSimilarityServiceConfigInjection:
    """测试配置注入"""

    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    @patch('app.services.llm_similarity_service.InputValidationConfig.prepare_tag_pair')
    def test_similarity_max_retries_config(self, mock_prepare_tag_pair, mock_metrics, mock_audit_logger):
        """验证SIMILARITY_MAX_RETRIES配置生效"""
        mock_prepare_tag_pair.return_value = ("config", "test")

        service = LLMSimilarityService()
        with (
            patch.object(settings, "SIMILARITY_MAX_RETRIES", 3),
            patch.object(service, "_calculate_with_retry", return_value=0.66) as mock_retry,
        ):
            result = service.calculate_tag_similarity_with_llm("CONFIG", "TEST")

        # 验证从settings注入到主流程
        assert result == 0.66
        mock_retry.assert_called_once()
        called_kwargs = mock_retry.call_args.kwargs
        assert called_kwargs["tag1"] == "config"
        assert called_kwargs["tag2"] == "test"
        assert called_kwargs["max_retries"] == 3
        assert isinstance(called_kwargs["trace_id"], str) and called_kwargs["trace_id"]


class TestLLMSimilarityServiceMainPath:
    """测试主流程链接"""

    @patch('app.services.llm_similarity_service.InputValidationConfig.prepare_tag_pair')
    @patch('app.services.llm_similarity_service.audit_logger')
    @patch('app.services.llm_similarity_service.metrics')
    def test_calculate_tag_similarity_with_llm_main_path(self, mock_metrics, mock_audit_logger, mock_prepare_tag_pair):
        """验证calculate_tag_similarity_with_llm主流程"""
        service = LLMSimilarityService()
        service.use_ollama = True
        service.use_openai = False

        # Mock输入验证
        mock_prepare_tag_pair.return_value = ("python", "programming")

        # Mock重试返回值
        with patch.object(service, '_calculate_with_retry', return_value=0.87):
            result = service.calculate_tag_similarity_with_llm(tag1="Python", tag2="Programming")

            # 应该返回float分值
            assert isinstance(result, float)
            assert 0.0 <= result <= 1.0
