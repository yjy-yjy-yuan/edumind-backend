"""
LLM 模型参数白名单与配置
- 参数化配置：所有参数（模型名、温度、top_p等）集中管理
- 白名单约束：只允许预定义的参数值，防止注入与滥用
- 默认低随机性：temperature 默认 0~0.2，避免分值抖动
"""

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator


# ============================================================================
# OpenAI 兼容 API 参数定义
# ============================================================================
class OpenAIModelType(str, Enum):
    """允许的 OpenAI 兼容模型列表"""

    QWEN_MAX = "qwen-max"  # 通义千问 Max（推荐）
    QWEN_PLUS = "qwen-plus"  # 通义千问 Plus
    QWEN_TURBO = "qwen-turbo"  # 通义千问 Turbo（最快）


class OllamaModelType(str, Enum):
    """允许的 Ollama 本地模型列表"""

    QWEN_35_7B = "qwen-2.5:7b"  # Qwen 2.5 7B（推荐平衡）
    QWEN_35_14B = "qwen-2.5:14b"  # Qwen 2.5 14B（精度优先）
    LLAMA_27B = "llama-2:7b"  # Llama 2 7B（通用）


@dataclass
class ModelParameterProfile:
    """
    单个模型的参数配置档案

    用途：
    - 为每个模型定义一套推荐参数
    - 确保不同模型间的行为一致性
    """

    model_name: str  # 模型标识
    provider: Literal["openai", "ollama"]  # 提供商
    temperature: float = 0.1  # 温度 [0, 1]，推荐 0~0.2
    top_p: float = 0.95  # Top-P 核采样
    max_tokens: int = 50  # 最大输出长度（短回复）
    timeout_sec: int = 10  # 超时时间（秒）
    retry_count: int = 1  # 失败重试次数
    enabled: bool = True  # 是否启用
    tags: List[str] = field(default_factory=list)  # 标签（用于查询）


# ============================================================================
# 白名单配置
# ============================================================================
class ModelParamWhitelist:
    """
    模型参数白名单

    功能：
    - 维护所有允许的模型与参数组合
    - 防止任意参数注入与配置滥用
    - 支持版本化的参数变更与回滚
    """

    # 指定 OpenAI 兼容模型的参数
    OPENAI_PROFILES = {
        "qwen-max": ModelParameterProfile(
            model_name="qwen-max",
            provider="openai",
            temperature=0.1,
            top_p=0.95,
            max_tokens=50,
            timeout_sec=15,
            retry_count=1,
            enabled=True,
            tags=["production", "recommended"],
        ),
        "qwen-plus": ModelParameterProfile(
            model_name="qwen-plus",
            provider="openai",
            temperature=0.1,
            top_p=0.95,
            max_tokens=50,
            timeout_sec=10,
            retry_count=1,
            enabled=True,
            tags=["balance"],
        ),
        "qwen-turbo": ModelParameterProfile(
            model_name="qwen-turbo",
            provider="openai",
            temperature=0.0,  # 更确定的输出
            top_p=1.0,
            max_tokens=30,
            timeout_sec=5,
            retry_count=0,
            enabled=True,
            tags=["fast", "greedy"],
        ),
    }

    # Ollama 本地模型的参数
    OLLAMA_PROFILES = {
        "qwen-2.5:7b": ModelParameterProfile(
            model_name="qwen-2.5:7b",
            provider="ollama",
            temperature=0.1,
            top_p=0.95,
            max_tokens=50,
            timeout_sec=10,
            retry_count=1,
            enabled=True,
            tags=["recommended", "balance"],
        ),
        "qwen-2.5:14b": ModelParameterProfile(
            model_name="qwen-2.5:14b",
            provider="ollama",
            temperature=0.1,
            top_p=0.95,
            max_tokens=50,
            timeout_sec=20,
            retry_count=1,
            enabled=True,
            tags=["accuracy"],
        ),
        "llama-2:7b": ModelParameterProfile(
            model_name="llama-2:7b",
            provider="ollama",
            temperature=0.2,
            top_p=0.9,
            max_tokens=50,
            timeout_sec=10,
            retry_count=1,
            enabled=True,
            tags=["general"],
        ),
    }

    @classmethod
    def get_openai_profile(cls, model_name: str) -> ModelParameterProfile:
        """
        获取 OpenAI 兼容模型的参数档案

        Args:
            model_name: 模型名称

        Returns:
            参数档案

        Raises:
            ValueError: 如果模型不在白名单中
        """
        if model_name not in cls.OPENAI_PROFILES:
            available = ", ".join(cls.OPENAI_PROFILES.keys())
            raise ValueError(f"OpenAI 模型 '{model_name}' 不在白名单中。" f"允许的模型: {available}")

        profile = cls.OPENAI_PROFILES[model_name]
        if not profile.enabled:
            raise ValueError(f"模型 '{model_name}' 已被禁用")

        return profile

    @classmethod
    def get_ollama_profile(cls, model_name: str) -> ModelParameterProfile:
        """
        获取 Ollama 本地模型的参数档案

        Args:
            model_name: 模型名称

        Returns:
            参数档案

        Raises:
            ValueError: 如果模型不在白名单中
        """
        if model_name not in cls.OLLAMA_PROFILES:
            available = ", ".join(cls.OLLAMA_PROFILES.keys())
            raise ValueError(f"Ollama 模型 '{model_name}' 不在白名单中。" f"允许的模型: {available}")

        profile = cls.OLLAMA_PROFILES[model_name]
        if not profile.enabled:
            raise ValueError(f"模型 '{model_name}' 已被禁用")

        return profile

    @classmethod
    def get_profile(cls, model_name: str, provider: str = None) -> ModelParameterProfile:
        """
        自动选择合适的获取方法

        Args:
            model_name: 模型名称
            provider: 提供商（"openai" 或 "ollama"），如果为 None 则自动推导

        Returns:
            参数档案
        """
        # 自动推导 provider
        if provider is None:
            if model_name in cls.OPENAI_PROFILES:
                provider = "openai"
            elif model_name in cls.OLLAMA_PROFILES:
                provider = "ollama"
            else:
                available_openai = ", ".join(cls.OPENAI_PROFILES.keys())
                available_ollama = ", ".join(cls.OLLAMA_PROFILES.keys())
                raise ValueError(
                    f"模型 '{model_name}' 不在任何白名单中。"
                    f"OpenAI 允许: {available_openai}。"
                    f"Ollama 允许: {available_ollama}"
                )

        if provider == "openai":
            return cls.get_openai_profile(model_name)
        elif provider == "ollama":
            return cls.get_ollama_profile(model_name)
        else:
            raise ValueError(f"未知的提供商: {provider}")

    @classmethod
    def list_openai_models(cls) -> List[str]:
        """列出所有允许的 OpenAI 模型"""
        return list(cls.OPENAI_PROFILES.keys())

    @classmethod
    def list_ollama_models(cls) -> List[str]:
        """列出所有允许的 Ollama 模型"""
        return list(cls.OLLAMA_PROFILES.keys())

    @classmethod
    def list_all_models(cls) -> List[str]:
        """列出所有允许的模型"""
        return list(cls.OPENAI_PROFILES.keys()) + list(cls.OLLAMA_PROFILES.keys())


# ============================================================================
# 输入验证配置（防注入）
# ============================================================================
class InputValidationConfig:
    """
    标签输入验证配置

    功能：
    - 长度限制：防止挤占 token
    - 字符清洗：移除危险字符
    - 分隔符转义：防止分隔符注入
    """

    # 标签长度限制
    MIN_TAG_LENGTH = 1
    MAX_TAG_LENGTH = 100  # 中文或英文，不超过 100 字符

    # 允许的字符范围（黑名单方式）
    FORBIDDEN_CHARS = {
        '\x00',  # 空字符
        '\t',
        '\n',
        '\r',  # 制表符、换行
        '\\',  # 反斜杠（可能导致转义问题）
    }

    # 分隔符（需要转义）
    SEPARATORS = {'/', '=', '|', ';', ','}

    @classmethod
    def sanitize_tag(cls, tag: str) -> str:
        """
        清洗标签，移除危险字符

        Args:
            tag: 原始标签

        Returns:
            清洁后的标签
        """
        if not isinstance(tag, str):
            raise TypeError(f"标签必须是字符串，得到 {type(tag)}")

        # 1. 移除禁止字符
        tag = ''.join(ch for ch in tag if ch not in cls.FORBIDDEN_CHARS)

        # 2. 移除前后空格
        tag = tag.strip()

        return tag

    @classmethod
    def validate_tag(cls, tag: str) -> None:
        """
        验证标签合法性

        Args:
            tag: 标签文本

        Raises:
            ValueError: 如果标签不合法
        """
        # 1. 类型检查
        if not isinstance(tag, str):
            raise ValueError(f"标签必须是字符串，得到 {type(tag)}")

        # 2. 长度检查
        if len(tag) < cls.MIN_TAG_LENGTH:
            raise ValueError(f"标签长度不能少于 {cls.MIN_TAG_LENGTH}")

        if len(tag) > cls.MAX_TAG_LENGTH:
            raise ValueError(f"标签长度不能超过 {cls.MAX_TAG_LENGTH}")

        # 3. 禁止字符检查
        forbidden_found = [ch for ch in tag if ch in cls.FORBIDDEN_CHARS]
        if forbidden_found:
            raise ValueError(f"标签包含禁止字符: {forbidden_found}")

    @classmethod
    def prepare_tag_pair(cls, tag1: str, tag2: str) -> tuple:
        """
        准备标签对，统一进行清洗与验证

        Args:
            tag1: 第一个标签
            tag2: 第二个标签

        Returns:
            (清洁后的 tag1, 清洁后的 tag2)

        Raises:
            ValueError: 如果标签不合法
        """
        # 清洗
        tag1 = cls.sanitize_tag(tag1)
        tag2 = cls.sanitize_tag(tag2)

        # 验证
        cls.validate_tag(tag1)
        cls.validate_tag(tag2)

        return tag1, tag2


# ============================================================================
# 全局默认配置
# ============================================================================
class SimilarityConfig:
    """相似度计算的全局配置"""

    # 默认模型（可通过环境变量或配置文件覆盖）
    DEFAULT_OPENAI_MODEL = "qwen-max"
    DEFAULT_OLLAMA_MODEL = "qwen-2.5:7b"

    # 默认提示词版本
    DEFAULT_PROMPT_VERSION = "v2"

    # 失败降级策略
    DEFAULT_FALLBACK_SCORE_ON_ERROR = None  # None 表示返回 None（调用方需要处理）
    DEFAULT_FALLBACK_SCORE_ON_TIMEOUT = 0.5  # 超时时返回中立值

    # 日志配置
    ENABLE_AUDIT_LOG = True  # 是否启用审计日志
    LOG_LEVEL = "INFO"  # 日志级别
