"""
相似度分数解析器与输入验证
- 唯一解析器：所有路径使用同一解析逻辑，禁止各自实现
- 强校验：正则严格，非数字返回失败
- 异常分型：timeout、provider_error、parse_error 等
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal
from typing import Optional
from typing import Tuple

logger = logging.getLogger(__name__)


class ParseErrorType(Enum):
    """解析错误类型分类"""

    INVALID_FORMAT = "parse_error_invalid_format"  # 格式不符合预期
    OUT_OF_RANGE = "parse_error_out_of_range"  # 分值超出 [0, 1] 范围
    NO_NUMBER = "parse_error_no_number"  # 找不到数字
    MULTIPLE_NUMBERS = "parse_error_multiple_numbers"  # 有多个数字（模糊）
    EMPTY_RESPONSE = "parse_error_empty_response"  # 空响应


class ProviderErrorType(Enum):
    """提供商错误类型分类"""

    TIMEOUT = "provider_error_timeout"
    CONNECTION_ERROR = "provider_error_connection_error"
    AUTH_ERROR = "provider_error_auth_error"
    RATE_LIMIT = "provider_error_rate_limit"
    INVALID_MODEL = "provider_error_invalid_model"
    SERVER_ERROR = "provider_error_server_error"
    OTHER = "provider_error_other"


@dataclass
class ParseResult:
    """
    解析结果容器

    用途：
    - 返回解析成功/失败的结构化结果
    - 记录原始文本、解析分值、错误类型等信息
    - 便于审计与调试
    """

    success: bool  # 解析是否成功
    score: Optional[float] = None  # 解析得到的分值 [0, 1]
    score_raw: Optional[str] = None  # 原始提取的数字字符串
    raw_response: Optional[str] = None  # 模型原始响应（去首尾空白后）
    error_type: Optional[str] = None  # 错误类型（ParseErrorType 值）
    error_message: Optional[str] = None  # 错误详情
    parse_time_ms: float = 0.0  # 解析耗时（毫秒）

    def __post_init__(self):
        """后初始化验证"""
        if self.success:
            if self.score is None:
                raise ValueError("成功的解析结果必须包含分值")
            if not (0.0 <= self.score <= 1.0):
                raise ValueError(f"分值必须在 [0, 1] 范围内，得到 {self.score}")

    def to_dict(self) -> dict:
        """将结果转换为字典"""
        return {
            "success": self.success,
            "score": self.score,
            "score_raw": self.score_raw,
            "raw_response": self.raw_response,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "parse_time_ms": self.parse_time_ms,
        }


class SimilarityScoreParser:
    """
    统一的相似度分数解析器

    支持的输出格式：
    1. 单浮点数：0.75
    2. 带单位前缀：score: 0.75
    3. 带 markdown 格式块：```0.75```

    失败返回 ParseResult(success=False, error_type=..., error_message=...)
    """

    # 解析模式
    # 注：所有模式现在支持可选的负号（用于处理 -0.1 这样的应该被拒绝的值）
    PATTERN_SINGLE_FLOAT = re.compile(r'^(-?\d+(?:\.\d+)?)$')
    PATTERN_SCORE_PREFIX = re.compile(r'score\s*[:\s=]+\s*(-?\d+(?:\.\d+)?)', re.IGNORECASE)
    PATTERN_CODE_BLOCK = re.compile(r'```\s*(-?\d+(?:\.\d+)?)\s*```')
    PATTERN_ANY_FLOAT = re.compile(r'(-?\d+(?:\.\d+)?)')

    @staticmethod
    def parse_v2_format(response: str) -> ParseResult:
        """
        解析 v2 格式：score: X.XX

        这是新版本（v2+）的标准输出格式。
        """
        import time

        start = time.time()

        response = response.strip() if response else ""

        # 1. 空响应检查
        if not response:
            return ParseResult(
                success=False,
                raw_response=response,
                error_type=ParseErrorType.EMPTY_RESPONSE.value,
                error_message="模型返回空响应",
                parse_time_ms=(time.time() - start) * 1000,
            )

        # 2. 尝试 "score: X.XX" 格式
        match = SimilarityScoreParser.PATTERN_SCORE_PREFIX.search(response)
        if match:
            score_str = match.group(1)
            try:
                score = float(score_str)
                if 0.0 <= score <= 1.0:
                    return ParseResult(
                        success=True,
                        score=score,
                        score_raw=score_str,
                        raw_response=response,
                        parse_time_ms=(time.time() - start) * 1000,
                    )
                else:
                    return ParseResult(
                        success=False,
                        score_raw=score_str,
                        raw_response=response,
                        error_type=ParseErrorType.OUT_OF_RANGE.value,
                        error_message=f"分值 {score} 超出范围 [0, 1]",
                        parse_time_ms=(time.time() - start) * 1000,
                    )
            except ValueError:
                pass

        # 3. 尝试单浮点数格式 (如果 response 完全是数字)
        match = SimilarityScoreParser.PATTERN_SINGLE_FLOAT.match(response)
        if match:
            score_str = match.group(1)
            try:
                score = float(score_str)
                # 检查范围
                if 0.0 <= score <= 1.0:
                    return ParseResult(
                        success=True,
                        score=score,
                        score_raw=score_str,
                        raw_response=response,
                        parse_time_ms=(time.time() - start) * 1000,
                    )
                else:
                    return ParseResult(
                        success=False,
                        score_raw=score_str,
                        raw_response=response,
                        error_type=ParseErrorType.OUT_OF_RANGE.value,
                        error_message=f"分值 {score} 超出范围 [0, 1]",
                        parse_time_ms=(time.time() - start) * 1000,
                    )
            except ValueError:
                pass  # 继续尝试其他格式

        # 4. 尝试代码块格式
        match = SimilarityScoreParser.PATTERN_CODE_BLOCK.search(response)
        if match:
            score_str = match.group(1)
            try:
                score = float(score_str)
                if 0.0 <= score <= 1.0:
                    return ParseResult(
                        success=True,
                        score=score,
                        score_raw=score_str,
                        raw_response=response,
                        parse_time_ms=(time.time() - start) * 1000,
                    )
            except ValueError:
                pass

        # 5. 最后尝试提取任意浮点数
        matches = list(SimilarityScoreParser.PATTERN_ANY_FLOAT.finditer(response))

        if not matches:
            return ParseResult(
                success=False,
                raw_response=response,
                error_type=ParseErrorType.NO_NUMBER.value,
                error_message=f"无法从响应中提取数字: {response}",
                parse_time_ms=(time.time() - start) * 1000,
            )

        # 如果有多个数字，返回歧义错误（安全起见）
        if len(matches) > 1:
            found_nums = [m.group(1) for m in matches]
            return ParseResult(
                success=False,
                raw_response=response,
                error_type=ParseErrorType.MULTIPLE_NUMBERS.value,
                error_message=f"响应中有多个数字，模糊不清: {found_nums}",
                parse_time_ms=(time.time() - start) * 1000,
            )

        # 单个数字，尝试转换
        score_str = matches[0].group(1)
        try:
            score = float(score_str)
            if 0.0 <= score <= 1.0:
                return ParseResult(
                    success=True,
                    score=score,
                    score_raw=score_str,
                    raw_response=response,
                    parse_time_ms=(time.time() - start) * 1000,
                )
            else:
                return ParseResult(
                    success=False,
                    score_raw=score_str,
                    raw_response=response,
                    error_type=ParseErrorType.OUT_OF_RANGE.value,
                    error_message=f"分值 {score} 超出范围 [0, 1]",
                    parse_time_ms=(time.time() - start) * 1000,
                )
        except ValueError:
            return ParseResult(
                success=False,
                score_raw=score_str,
                raw_response=response,
                error_type=ParseErrorType.INVALID_FORMAT.value,
                error_message=f"无法转换为浮点数: {score_str}",
                parse_time_ms=(time.time() - start) * 1000,
            )

    @staticmethod
    def parse(response: str, prompt_version: str = "v2") -> ParseResult:
        """
        通用解析接口

        根据提示词版本选择合适的解析策略。

        Args:
            response: 模型原始响应（通常已去首尾空白）
            prompt_version: 提示词版本（"v1", "v2" 等）

        Returns:
            ParseResult 对象（保证不抛异常）
        """
        try:
            # 目前 v1 与 v2 都支持相同的解析（单浮点或 score: X.XX）
            # 未来版本如果改变输出格式，可在此根据版本号分发
            return SimilarityScoreParser.parse_v2_format(response)
        except Exception as e:
            # 防守性编程：捕获任何异常，返回失败结果
            logger.error(f"Parser exception (unexpected): {e}")
            return ParseResult(
                success=False,
                raw_response=response,
                error_type=ParseErrorType.INVALID_FORMAT.value,
                error_message=f"Parser internal error: {str(e)}",
            )

    @staticmethod
    def normalize_score(score: float) -> float:
        """
        正规化分值到 [0, 1] 范围

        用途：
        - 防御模型返回超出范围的值
        - 在日志中记录 raw 值和正规化值

        Args:
            score: 原始分值

        Returns:
            正规化后的分值
        """
        return max(0.0, min(1.0, score))


# ============================================================================
# 输入验证与清洗（配合 config_model_params 的 InputValidationConfig）
# ============================================================================
class TagInputValidator:
    """标签输入验证器"""

    @staticmethod
    def validate_and_prepare(tag1: str, tag2: str) -> Tuple[str, str]:
        """
        验证和准备输入标签

        Args:
            tag1: 第一个标签
            tag2: 第二个标签

        Returns:
            (清洁后的 tag1, 清洁后的 tag2)

        Raises:
            ValueError: 如果输入不合法
        """
        from app.services.config_model_params import InputValidationConfig

        return InputValidationConfig.prepare_tag_pair(tag1, tag2)

    @staticmethod
    def sanitize(tag: str) -> str:
        """清洗单个标签"""
        from app.services.config_model_params import InputValidationConfig

        return InputValidationConfig.sanitize_tag(tag)
