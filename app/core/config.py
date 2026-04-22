"""配置管理 - 使用 Pydantic Settings"""

import os
from functools import lru_cache
from pathlib import Path
from typing import List
from typing import Optional
from typing import Set
from typing import Union

from pydantic import field_validator
from pydantic_settings import BaseSettings

ENV_FILE_PATH = str(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    """应用配置"""

    # 环境配置 (local/development/production)
    APP_ENV: str = "local"

    # 应用配置
    APP_NAME: str = "EduMind"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 2004  # FastAPI 端口
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    # 认证 token（HMAC，与 app/utils/auth_token.py 一致）
    AUTH_TOKEN_TTL_SECONDS: int = 604800  # 默认 7 天
    AUTH_TOKEN_CLOCK_SKEW_SECONDS: int = 120  # 校验过期时允许的时钟偏差（秒）
    # 为 True 时允许仅凭 query/body 的 user_id 识别用户（迁移/联调）；生产环境应为 False，仅信任 Bearer
    AUTH_ALLOW_LEGACY_USER_ID_ONLY: bool = False
    # 智能体编排：学习流 token 预算（估算）、治理审计开关
    AGENT_LEARNING_FLOW_TOKEN_BUDGET: int = 8000
    AGENT_GOVERNANCE_AUDIT_ENABLED: bool = True
    AUTO_CREATE_TABLES: bool = False
    BACKGROUND_TASK_EXECUTOR: str = "auto"
    BACKGROUND_TASK_WORKERS: int = 2

    # 数据库配置 (MySQL)
    DATABASE_URL: str = "mysql+pymysql://root:password@127.0.0.1:3306/edumind"

    # LLM API 配置 (通义千问/OpenAI兼容)
    # 注意: 敏感密钥请在 .env 文件中配置，不要硬编码
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_API_KEY: str = ""
    QWEN_API_BASE: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com/v1"
    QA_DEFAULT_PROVIDER: str = "qwen"
    QWEN_QA_MODEL: str = "qwen-plus"
    DEEPSEEK_QA_MODEL: str = "deepseek-chat"
    DEEPSEEK_REASONER_MODEL: str = "deepseek-reasoner"
    QA_TOP_K: int = 4
    QA_MAX_CONTEXT_CHARS: int = 4500
    QA_MAX_HISTORY_MESSAGES: int = 8
    QA_MAX_HISTORY_CHARS: int = 2200

    # Sleek 设计能力配置
    SLEEK_API_KEY: str = ""
    SLEEK_API_BASE: str = "https://sleek.design"
    SLEEK_PROJECT_LIMIT: int = 50
    SLEEK_POLL_TIMEOUT_SECONDS: int = 300
    SLEEK_POLL_INITIAL_INTERVAL_SECONDS: int = 2
    SLEEK_POLL_BACKOFF_AFTER_SECONDS: int = 10
    SLEEK_POLL_BACKOFF_INTERVAL_SECONDS: int = 5

    # Ollama 配置
    OLLAMA_BASE_URL: str = "http://localhost:11434/api"
    OLLAMA_MODEL: str = "qwen-3.5:9b"

    # 文件上传配置
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    UPLOAD_FOLDER: str = ""
    SUBTITLE_FOLDER: str = ""
    PREVIEW_FOLDER: str = ""
    TEMP_FOLDER: str = ""
    MAX_CONTENT_LENGTH: int = 500 * 1024 * 1024  # 500MB
    ALLOWED_EXTENSIONS: Set[str] = {"mp4", "avi", "mov", "mkv", "webm", "flv"}

    # Whisper 配置 (可选: tiny, base, small, medium, large, turbo)
    WHISPER_MODEL: str = "base"
    WHISPER_MODEL_PATH: str = "/Users/yuan/302_works/whisper_models"
    WHISPER_PRELOAD_ON_STARTUP: bool = True
    WHISPER_LOAD_TIMEOUT_SECONDS: int = 60
    WHISPER_DOWNLOAD_TIMEOUT_SECONDS: int = 300

    # 视频推荐：站内候选上限（避免全表扫描）、站外默认开关、站外 HTTP 超时与抓取策略
    RECOMMENDATION_MAX_CANDIDATES_SCAN: int = 400
    RECOMMENDATION_INCLUDE_EXTERNAL_DEFAULT: bool = False
    RECOMMENDATION_EXTERNAL_TIMEOUT_SECONDS: float = 8.0
    RECOMMENDATION_EXTERNAL_FETCH_PARALLEL: bool = True
    RECOMMENDATION_EXTERNAL_FETCH_RETRIES: int = 1
    # 推荐页对站外候选自动入库（仅登录用户生效），入库后返回可直接打开的视频详情项
    RECOMMENDATION_AUTO_IMPORT_EXTERNAL: bool = True
    RECOMMENDATION_AUTO_IMPORT_MAX_ITEMS: int = 2
    # 推荐相似度约束（仅后端使用，不对前端暴露分值）
    RECOMMENDATION_SIMILARITY_MIN_SCORE: float = 0.55
    # 推荐返回条数窗口：前端体验目标 6~8
    RECOMMENDATION_RETURN_MIN_ITEMS: int = 6
    RECOMMENDATION_RETURN_MAX_ITEMS: int = 8
    # 推荐结果标题黑名单关键词（逗号分隔）；命中后将从对外推荐结果中移除
    RECOMMENDATION_EXCLUDED_TITLE_KEYWORDS: str = "排列组合插空法详解"
    # 推荐 API 契约版本（响应体 contract_version，与 docs 中 Recommendation Contract 对齐）
    # v2：不再返回 seed_video_title（与 seed_video_id 冗余）；设为 "1" 可恢复旧字段
    RECOMMENDATION_CONTRACT_VERSION: str = "2"
    # 推荐域是否写入 app.analytics.telemetry（结构化 JSON 行）
    RECOMMENDATION_TELEMETRY_ENABLED: bool = True
    # 推荐运营聚合 API 的内存事件缓冲上限（DB 异常时作为降级来源）
    RECOMMENDATION_OPS_EVENT_BUFFER_SIZE: int = 5000

    # 语义搜索配置
    SEARCH_ENABLED: bool = False
    SEARCH_BACKEND: str = "gemini"
    SEARCH_GEMINI_API_KEY: Optional[str] = None
    SEARCH_CHROMA_DB_DIR: str = "./data/chroma"
    SEARCH_CHROMA_ANONYMIZED_TELEMETRY: bool = False
    SEARCH_CHUNK_DURATION: int = 30
    SEARCH_CHUNK_OVERLAP: int = 5
    SEARCH_EMBEDDING_DIM: int = 768
    SEARCH_SIMILARITY_THRESHOLD: float = 0.5

    # 标签相似度计算配置（LLM路径）
    SIMILARITY_MAX_RETRIES: int = 2  # 标签相似度LLM计算最多重试次数
    SIMILARITY_PROMPT_VERSION: str = "v2"  # 版本化提示词版本
    SEARCH_LOCAL_MODEL: str = "qwen8b"
    SEARCH_PREPROCESS: bool = True
    SEARCH_PREPROCESS_RESOLUTION: int = 480
    SEARCH_PREPROCESS_FPS: int = 5
    SEARCH_SKIP_STILL_FRAMES: bool = True
    SEARCH_AUTO_INDEX_NEW_VIDEOS: bool = True
    SEARCH_MAX_RESULTS: int = 20

    # 语义搜索索引启动模式配置
    # 说明: SEARCH_ENABLED=true && SEARCH_AUTO_INDEX_NEW_VIDEOS=true 时，按以下模式决定索引启动时机
    # - "after_video_completed": 保持当前行为，先 VideoStatus.COMPLETED，再异步提交 index_video_for_search
    # - "inline_after_subtitle": 在字幕文件已落盘且 subtitle_filepath 可用后启动内嵌索引，允许与摘要/标签并行
    SEARCH_INDEX_STARTUP_MODE: str = "after_video_completed"
    # 内嵌索引模式下，主处理流程等待索引完成的最大超时（秒）；-1 表示不等待
    SEARCH_INLINE_INDEX_WAIT_TIMEOUT_SECONDS: int = 30
    # 内嵌索引失败策略:
    # - "mark_completed_without_index": 主流程仍可 COMPLETED，索引失败时 has_semantic_index=false
    # - "require_index_success": 索引失败则不进入 COMPLETED（需明确前端展示）
    SEARCH_INLINE_INDEX_FAIL_POLICY: str = "mark_completed_without_index"

    # 集中式遥测管道（app.analytics）
    ANALYTICS_LOG_LEVEL: str = "INFO"  # DEBUG|INFO|WARNING|ERROR — 作用于 app.analytics.telemetry
    ANALYTICS_ALERT_MAX_FAILURE_RATE: float = 0.15
    ANALYTICS_ALERT_MAX_TIMEOUT_RATE: float = 0.10
    ANALYTICS_ALERT_LATENCY_TIMEOUT_MS: float = 30_000.0
    ANALYTICS_ALERT_DRIFT_REL_THRESHOLD: float = 0.10
    # 同一告警键（如 failure_rate:search）的最小重复输出间隔（秒），抑制高流量下刷屏
    ANALYTICS_ALERT_MIN_INTERVAL_SEC: float = 60.0
    # 未透传上游 trace_id 时写入事件的占位符（metadata.trace_id_source=missing）
    ANALYTICS_TRACE_ID_PLACEHOLDER: str = "unset"

    # Compounding 导出脱敏配置（P1-3）
    COMPOUNDING_USER_ID_HASH_SALT: str = "edumind_compounding_v1"
    COMPOUNDING_QUERY_TEXT_MAX_CHARS: int = 200
    COMPOUNDING_TAG_MAX_CHARS: int = 64
    COMPOUNDING_ERROR_MESSAGE_MAX_CHARS: int = 120

    # 自适应切片配置
    SEARCH_ADAPTIVE_CHUNKING: bool = True  # 是否启用自适应切片
    # 自适应参数规则：(max_duration_inclusive, chunk_duration, overlap)
    # 使用单值上限，遍历时返回第一个匹配的规则，完全避免边界歧义。
    # 含义：若 duration <= max_duration_inclusive，则使用该参数
    SEARCH_ADAPTIVE_PARAMS: List[tuple] = [
        (180, 12, 2),  # duration <= 180s (3min):     12s chunk, 2s overlap
        (600, 20, 4),  # duration <= 600s (10min):    20s chunk, 4s overlap
        (1800, 45, 8),  # duration <= 1800s (30min):   45s chunk, 8s overlap
        (3600, 60, 10),  # duration <= 3600s (60min):   60s chunk, 10s overlap
        (999999, 75, 12),  # duration > 3600s (兜底):      75s chunk, 12s overlap
    ]

    # CORS 配置 (允许前端访问) - 使用字符串，支持逗号分隔
    CORS_ORIGINS: Union[str, List[str]] = (
        "null,http://localhost:328,http://127.0.0.1:328,http://localhost:5173,http://127.0.0.1:5173"
    )

    # Redis 配置 (可选，保留用于生产环境)
    REDIS_URL: str = "redis://localhost:6379/0"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """解析 CORS_ORIGINS，支持逗号分隔的字符串或列表"""
        if isinstance(v, str):
            origins = [origin.strip() for origin in v.split(",") if origin.strip()]
        else:
            origins = list(v)
        if "null" not in origins:
            origins.append("null")
        return origins

    class Config:
        env_file = ENV_FILE_PATH
        case_sensitive = True
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 设置文件夹路径
        if not self.UPLOAD_FOLDER:
            self.UPLOAD_FOLDER = os.path.join(self.BASE_DIR, "uploads")
        if not self.SUBTITLE_FOLDER:
            self.SUBTITLE_FOLDER = os.path.join(self.UPLOAD_FOLDER, "subtitles")
        if not self.PREVIEW_FOLDER:
            self.PREVIEW_FOLDER = os.path.join(self.BASE_DIR, "previews")
        if not self.TEMP_FOLDER:
            self.TEMP_FOLDER = os.path.join(self.BASE_DIR, "temp")


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


settings = get_settings()
