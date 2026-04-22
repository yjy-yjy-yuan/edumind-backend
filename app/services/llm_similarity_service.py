"""
基于LLM的标签相似度计算服务（企业级版本）

关键改进：
1. 提示词版本管理 - 单一事实源
2. 参数白名单约束 - 配置化、防注入
3. 统一解析器 - 两路径一致性
4. 结构化审计日志 - 可观测与追溯
5. 失败重试与降级 - 稳健性

【P0】边界清晰化：本服务仅用于"标签相似度"，不直接替代主搜索排序
【P0】单一事实源：所有提示词从 tag_similarity_prompts 集中获取
【P0】版本治理：每次提示词变更都有版本号、变更说明、回滚目标
"""

import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import requests
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.config_model_params import InputValidationConfig
from app.services.config_model_params import ModelParamWhitelist
from app.services.config_model_params import SimilarityConfig
from app.services.similarity_analytics import SimilarityAuditLog
from app.services.similarity_analytics import SimilarityAuditLogger
from app.services.similarity_analytics import SimilarityMetrics
from app.services.similarity_score_parser import ParseResult
from app.services.similarity_score_parser import SimilarityScoreParser
from app.services.similarity_score_parser import TagInputValidator
from app.services.similarity_service_container import get_persistence_service

# 导入新的模块化组件
from app.services.tag_similarity_prompts import TagSimilarityPromptFactory
from app.utils.ollama_compat import build_ollama_options
from app.utils.ollama_compat import sanitize_ollama_response_text
from openai import OpenAI

# 配置日志
logger = logging.getLogger(__name__)
audit_logger = SimilarityAuditLogger("similarity_audit")
metrics = SimilarityMetrics()


def _record_similarity_audit_log(audit_log: SimilarityAuditLog) -> None:
    """将审计日志写入持久化服务（内存 + DB）；未初始化或会话/DB 异常时回退到进程内 metrics。"""
    try:
        try:
            persistence = get_persistence_service()
        except RuntimeError:
            metrics.record_log(audit_log)
            return
        db = SessionLocal()
        try:
            persistence.record_log(audit_log, db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "similarity audit log persistence failed, falling back to memory metrics: %s",
            exc,
        )
        metrics.record_log(audit_log)


class LLMSimilarityService:
    """
    基于LLM的标签相似度计算服务（企业级版本）

    功能职责：
    - 评估两个标签的语义相似度（0-1）
    - 支持 OpenAI 兼容 API 和 Ollama 本地部署
    - 提供完整的审计追踪与性能监控
    - 支持失败重试与可解释降级
    """

    def __init__(self):
        """初始化服务"""
        self.openai_client = None
        self.use_ollama = False
        self.use_openai = False

        # 配置提示词版本（从配置读取，支持动态切换）
        self.prompt_version = SimilarityConfig.DEFAULT_PROMPT_VERSION

        # 初始化 OpenAI 客户端
        self._init_openai()

        # 检查 Ollama 可用性
        self._init_ollama()

        if not self.use_openai and not self.use_ollama:
            logger.warning("⚠️ OpenAI 和 Ollama 服务都不可用，某些功能可能受限")

    def _init_openai(self) -> None:
        """初始化 OpenAI 客户端（带参数白名单检查）"""
        try:
            if settings.OPENAI_API_KEY:
                self.openai_client = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                # 验证配置的模型在白名单中
                model = getattr(settings, 'OPENAI_SIMILARITY_MODEL', SimilarityConfig.DEFAULT_OPENAI_MODEL)
                try:
                    ModelParamWhitelist.get_openai_profile(model)
                    self.use_openai = True
                    logger.info(f"✅ OpenAI 初始化成功（模型: {model}）")
                except ValueError as e:
                    logger.error(f"❌ OpenAI 模型不在白名单中: {e}")
            else:
                logger.warning("未配置 OPENAI_API_KEY")
        except Exception as e:
            logger.error(f"❌ OpenAI 初始化失败: {e}")

    def _init_ollama(self) -> None:
        """初始化 Ollama 连接（健康检查）"""
        try:
            response = requests.get(f"{settings.OLLAMA_BASE_URL}/tags", timeout=5)
            if response.status_code == 200:
                # 验证配置的模型在白名单中
                model = getattr(settings, 'OLLAMA_SIMILARITY_MODEL', SimilarityConfig.DEFAULT_OLLAMA_MODEL)
                try:
                    ModelParamWhitelist.get_ollama_profile(model)
                    self.use_ollama = True
                    logger.info(f"✅ Ollama 可用（模型: {model}）")
                except ValueError as e:
                    logger.error(f"❌ Ollama 模型不在白名单中: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Ollama 不可用: {e}")

    def calculate_tag_similarity_with_llm(self, tag1: str, tag2: str) -> float:
        """
        【核心方法】使用 LLM 计算两个标签的相似度

        args：
            tag1: 第一个标签
            tag2: 第二个标签

        Returns:
            相似度分数 [0.0, 1.0]
        """
        # 1. 快路径：处理空值和完全相同
        if tag1 is None or tag2 is None:
            return 0.0

        if tag1.lower() == tag2.lower():
            return 1.0

        if tag1.lower() in tag2.lower() or tag2.lower() in tag1.lower():
            return 0.9

        # 2. 生成审计追踪
        trace_id = str(uuid.uuid4())[:8]

        # 3. 输入验证与清洗
        try:
            tag1_clean, tag2_clean = InputValidationConfig.prepare_tag_pair(tag1, tag2)
        except ValueError as e:
            logger.warning(f"[{trace_id}] 输入验证失败: {e}")
            return SimilarityConfig.DEFAULT_FALLBACK_SCORE_ON_ERROR or 0.0

        # 4. 尝试 LLM 计算（带重试）
        # 从配置获取重试次数，默认2次
        max_retries = getattr(settings, 'SIMILARITY_MAX_RETRIES', 2)
        score = self._calculate_with_retry(
            trace_id=trace_id,
            tag1=tag1_clean,
            tag2=tag2_clean,
            max_retries=max_retries,
        )

        return score

    def _calculate_with_retry(self, trace_id: str, tag1: str, tag2: str, max_retries: int = 1) -> float:
        """
        带重试机制的 LLM 计算

        流程（对每次重试）：
        1. 尝试 Ollama（如果可用）
        2. 失败则尝试 OpenAI（如果可用）

        如果所有重试都失败，则降级处理。

        Args:
            max_retries: 最多重试次数（包括初次尝试）
        """
        import time

        last_exception = None
        call_start_time = time.time()

        for attempt in range(max(1, max_retries)):  # 至少尝试1次
            try:
                audit_log = audit_logger.log_call_start(
                    trace_id=trace_id,
                    tag1=tag1,
                    tag2=tag2,
                    prompt_version=self.prompt_version,
                    provider="ollama" if self.use_ollama else "openai",
                    model=(
                        getattr(settings, 'OLLAMA_SIMILARITY_MODEL', SimilarityConfig.DEFAULT_OLLAMA_MODEL)
                        if self.use_ollama
                        else getattr(settings, 'OPENAI_SIMILARITY_MODEL', SimilarityConfig.DEFAULT_OPENAI_MODEL)
                    ),
                )
                audit_log.retry_count = attempt + 1  # 记录重试次数（从1开始：\"第1次尝试\"）

                # 尝试 Ollama
                if self.use_ollama:
                    try:
                        provider_start_time = time.perf_counter()
                        result = self._calculate_similarity_with_ollama(trace_id, tag1, tag2)
                        total_elapsed = (time.perf_counter() - provider_start_time) * 1000  # ms

                        if result is not None:
                            score, parse_elapsed = result  # 正确解包 (score, parse_elapsed)
                            provider_latency = max(0.0, total_elapsed - parse_elapsed)  # 纯provider耗时，下限保护
                            audit_logger.log_success(
                                audit_log,
                                score=score,
                                score_raw=str(score),
                                provider_latency_ms=provider_latency,
                                parse_latency_ms=parse_elapsed,  # 传真实延迟
                            )
                            _record_similarity_audit_log(audit_log)
                            return score
                    except Exception as e:
                        logger.debug(f"[{trace_id}] Ollama 失败（尝试 {attempt+1}/{max_retries}）: {e}")
                        last_exception = e

                # 尝试 OpenAI
                if self.use_openai:
                    try:
                        provider_start_time = time.perf_counter()
                        result = self._calculate_similarity_with_openai(trace_id, tag1, tag2)
                        total_elapsed = (time.perf_counter() - provider_start_time) * 1000  # ms

                        if result is not None:
                            score, parse_elapsed = result  # 正确解包 (score, parse_elapsed)
                            provider_latency = max(0.0, total_elapsed - parse_elapsed)  # 纯provider耗时，下限保护
                            audit_logger.log_success(
                                audit_log,
                                score=score,
                                score_raw=str(score),
                                provider_latency_ms=provider_latency,
                                parse_latency_ms=parse_elapsed,  # 传真实延迟
                            )
                            _record_similarity_audit_log(audit_log)
                            return score
                    except Exception as e:
                        logger.debug(f"[{trace_id}] OpenAI 失败（尝试 {attempt+1}/{max_retries}）: {e}")
                        last_exception = e

                # 如果本次尝试失败，等待后重试
                if attempt < max_retries - 1:
                    time.sleep(0.5)  # 500ms 的退避延迟

            except Exception as e:
                logger.error(f"[{trace_id}] 重试循环内部错误: {e}")
                last_exception = e

        # 所有重试都失败，降级处理
        total_elapsed = (time.time() - call_start_time) * 1000  # ms
        logger.warning(f"[{trace_id}] LLM 计算失败（{max_retries} 次重试），使用降级值，总耗时 {total_elapsed:.0f}ms")
        audit_log_final = audit_logger.log_call_start(
            trace_id=trace_id,
            tag1=tag1,
            tag2=tag2,
            prompt_version=self.prompt_version,
            provider="fallback",
            model="fallback",
        )
        audit_logger.log_fallback(
            audit_log_final,
            fallback_reason="llm_unavailable_after_retries",
            fallback_score=SimilarityConfig.DEFAULT_FALLBACK_SCORE_ON_ERROR,
        )
        _record_similarity_audit_log(audit_log_final)
        return SimilarityConfig.DEFAULT_FALLBACK_SCORE_ON_ERROR or 0.0

    def _calculate_similarity_with_ollama(self, trace_id: str, tag1: str, tag2: str) -> Optional[Tuple[float, float]]:
        """
        使用 Ollama 本地模型计算相似度

        返回: (score, parse_elapsed_ms) 元组，或 None 表示失败

        【P0】使用版本化提示词，不允许硬编码
        【P0】参数从白名单读取
        """
        try:
            # 1. 从工厂获取版本化提示词（单一事实源）
            prompt = TagSimilarityPromptFactory.get_ollama_prompt(tag1, tag2, self.prompt_version)

            # 2. 获取参数白名单中的配置
            model = getattr(settings, 'OLLAMA_SIMILARITY_MODEL', SimilarityConfig.DEFAULT_OLLAMA_MODEL)
            profile = ModelParamWhitelist.get_ollama_profile(model)

            # 3. 调用 Ollama 生成
            start = time.time()
            response = requests.post(
                f"{settings.OLLAMA_BASE_URL}/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": build_ollama_options(temperature=profile.temperature),
                },
                timeout=profile.timeout_sec,
            )
            elapsed_ms = (time.time() - start) * 1000

            if response.status_code != 200:
                logger.warning(f"[{trace_id}] Ollama API 错误: {response.status_code}")
                return None

            response_text = sanitize_ollama_response_text(response.json().get("response", "")).strip()

            # 4. 使用统一解析器（禁止各自解析）
            parse_start = time.time()
            parse_result = SimilarityScoreParser.parse(response_text, self.prompt_version)
            parse_elapsed = (time.time() - parse_start) * 1000

            if parse_result.success:
                logger.info(f"[{trace_id}] Ollama 计算成功: {parse_result.score}")
                return (parse_result.score, parse_elapsed)  # 返回元组，包含parse时间
            else:
                logger.warning(f"[{trace_id}] 解析失败（{parse_result.error_type}）: {parse_result.error_message}")
                return None

        except Exception as e:
            logger.error(f"[{trace_id}] Ollama 异常: {e}")
            return None

    def _calculate_similarity_with_openai(self, trace_id: str, tag1: str, tag2: str) -> Optional[Tuple[float, float]]:
        """
        使用 OpenAI 兼容 API 计算相似度

        返回: (score, parse_elapsed_ms) 元组，或 None 表示失败

        【P0】system 放规则，user 放输入（分离结构）
        【P0】模型从白名单读取（配置化）
        """
        if not self.openai_client:
            return None

        try:
            # 1. 从工厂获取版本化提示词（system 和 user 分离）
            system_prompt = TagSimilarityPromptFactory.get_system_prompt(self.prompt_version)
            user_prompt = TagSimilarityPromptFactory.get_user_prompt(tag1, tag2, self.prompt_version)

            # 2. 获取参数白名单中的配置
            model = getattr(settings, 'OPENAI_SIMILARITY_MODEL', SimilarityConfig.DEFAULT_OPENAI_MODEL)
            profile = ModelParamWhitelist.get_openai_profile(model)

            # 3. 调用 OpenAI 兼容 API
            start = time.time()
            response = self.openai_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=profile.temperature,
                top_p=profile.top_p,
                max_tokens=profile.max_tokens,
            )
            elapsed_ms = (time.time() - start) * 1000

            response_text = response.choices[0].message.content.strip()

            # 4. 使用统一解析器
            parse_start = time.time()
            parse_result = SimilarityScoreParser.parse(response_text, self.prompt_version)
            parse_elapsed = (time.time() - parse_start) * 1000

            if parse_result.success:
                logger.info(f"[{trace_id}] OpenAI 计算成功: {parse_result.score}")
                return (parse_result.score, parse_elapsed)  # 返回元组，包含parse时间
            else:
                logger.warning(f"[{trace_id}] 解析失败（{parse_result.error_type}）: {parse_result.error_message}")
                return None

        except Exception as e:
            logger.error(f"[{trace_id}] OpenAI 异常: {e}")
            return None

    def _calculate_string_similarity(self, s1: str, s2: str) -> float:
        """
        降级方案：字符串相似度（Jaccard）

        仅在 LLM 完全不可用时使用
        """
        if not s1 or not s2:
            return 0.0

        # 中文按字分割，英文按词分割
        if any('\u4e00' <= ch <= '\u9fff' for ch in s1 + s2):
            set1 = set(s1)
            set2 = set(s2)
        else:
            set1 = set(s1.split())
            set2 = set(s2.split())

        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))

        return intersection / union if union > 0 else 0.0

    # ==================== 保留向后兼容接口 ====================

    def calculate_tag_sets_similarity_direct(self, tags1: List[str], tags2: List[str]) -> float:
        """
        直接计算两组标签整体相似度（保留接口）
        """
        if not tags1 or not tags2:
            return 0.0

        if set(tags1) == set(tags2):
            return 1.0

        # 计算最佳匹配
        similarities = []
        for tag1 in tags1:
            best_similarity = max(
                [self.calculate_tag_similarity_with_llm(tag1, tag2) for tag2 in tags2],
                default=0,
            )
            similarities.append(best_similarity)

        for tag2 in tags2:
            best_similarity = max(
                [self.calculate_tag_similarity_with_llm(tag1, tag2) for tag1 in tags1],
                default=0,
            )
            similarities.append(best_similarity)

        return sum(similarities) / len(similarities) if similarities else 0.0

    def calculate_tag_sets_similarity_logic(self, tags1: List[str], tags2: List[str]) -> float:
        """逻辑计算标签组相似度（保留接口）"""
        return self.calculate_tag_sets_similarity_direct(tags1, tags2)

    def calculate_tag_sets_similarity(self, tags1: List[str], tags2: List[str]) -> float:
        """计算两组标签相似度（保留接口）"""
        return self.calculate_tag_sets_similarity_direct(tags1, tags2)

    def find_similar_videos(
        self, target_tags: List[str], all_videos: List[Dict[str, Any]], threshold: float = 0.7, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        查找相似视频（保留接口）
        """
        if not target_tags or not all_videos:
            return []

        similar_videos = []

        for video in all_videos:
            video_tags = []

            # 获取视频标签
            if video.get('tags'):
                try:
                    if isinstance(video['tags'], str):
                        video_tags = json.loads(video['tags'])
                    else:
                        video_tags = video['tags']
                except json.JSONDecodeError:
                    logger.warning(f"视频标签格式无效: {video.get('tags')}")
                    continue

            if not video_tags:
                continue

            # 计算相似度
            similarity = self.calculate_tag_sets_similarity(target_tags, video_tags)

            if similarity >= threshold:
                similar_videos.append({'video': video, 'similarity': similarity})

        # 排序并限制
        similar_videos.sort(key=lambda x: x['similarity'], reverse=True)
        return similar_videos[:limit]

    # ==================== 诊断与监控接口 ====================

    def get_metrics_for_day(self, date: str = None) -> Dict[str, Any]:
        """获取指定日期的性能指标"""
        try:
            persistence = get_persistence_service()
        except RuntimeError:
            return metrics.get_stats_for_day(date)
        db = SessionLocal()
        try:
            return persistence.get_daily_stats(date, db)
        finally:
            db.close()

    def check_score_drift(self, date: str = None, baseline: float = 0.5, threshold: float = 0.1) -> Dict[str, Any]:
        """检测分值分布漂移"""
        stats = self.get_metrics_for_day(date)
        target_date = stats.get("date") or date or datetime.utcnow().date().isoformat()
        avg_score = stats.get("avg_score")
        if avg_score is None:
            return {"date": target_date, "drift_detected": False, "reason": "No valid scores"}

        eps = 1e-9
        drift_abs = abs(avg_score - baseline)
        drift_pct = drift_abs / baseline if baseline != 0 else 0
        drift_detected = (drift_pct - threshold) > eps

        return {
            "date": target_date,
            "drift_detected": drift_detected,
            "baseline_mean": baseline,
            "actual_mean": avg_score,
            "drift_abs": drift_abs,
            "drift_pct": drift_pct,
            "threshold": threshold,
        }

    def get_prompt_version(self) -> str:
        """获取当前提示词版本"""
        return self.prompt_version

    def set_prompt_version(self, version: str) -> None:
        """
        切换提示词版本（支持灰度发布与回滚）

        Args:
            version: 新版本号 ("v1", "v2", ...)

        Raises:
            ValueError: 如果版本不存在
        """
        available = TagSimilarityPromptFactory.list_versions()
        if version not in available:
            raise ValueError(f"版本 '{version}' 不存在。可用: {available}")

        old_version = self.prompt_version
        self.prompt_version = version
        logger.info(f"提示词版本从 {old_version} 切换为 {version}")


# ============================================================================
# 全局实例
# ============================================================================
llm_similarity_service = LLMSimilarityService()
