"""配置管理 - 使用 Pydantic Settings"""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Set, Union

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
    # 本地开发默认用户邮箱（仅用于未登录时的开发兜底身份）。
    DEV_DEFAULT_USER_EMAIL: str = ""
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

    # Vinci 微服务接入配置（独立部署，不与主后端共环境）
    # [Deprecated] VINCI_ENABLED: Vinci 已降级为历史兼容路径，默认禁用
    VINCI_ENABLED: bool = False
    VINCI_BASE_URL: str = "http://127.0.0.1:8010"
    VINCI_API_KEY: str = ""
    VINCI_CHAT_PATH: str = "/api/v1/chat"
    VINCI_STREAM_PATH: str = "/api/v1/chat/stream"
    VINCI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    VINCI_CONNECT_TIMEOUT_SECONDS: float = 8.0
    VINCI_STREAM_TIMEOUT_SECONDS: float = 120.0
    VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 3
    VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS: float = 30.0

    # 实时画面描述配置（EduMind Frame Description Service）
    FRAME_DESC_ENABLED: bool = False
    # 实时画面描述上游: qwen3vl（推荐：轻量视觉描述服务）| vinci（历史兼容重型服务）
    FRAME_DESC_BACKEND: str = "qwen3vl"
    # 采样策略：固定间隔（fixed_interval）| 智能采样（smart）
    FRAME_DESC_SAMPLE_MODE: str = "fixed_interval"
    # 固定间隔模式下的采样周期（秒）；智能模式也会用此值作为兜底最小间隔
    FRAME_DESC_SAMPLE_INTERVAL_SECONDS: float = 3.0
    # 帧描述服务超时（秒）
    FRAME_DESC_TIMEOUT_SECONDS: float = 8.0
    # 推理前是否快速探测视觉模型服务可达性（不可达时直接降级，避免长时间卡在 connecting）
    FRAME_DESC_PROBE_UPSTREAM_BEFORE_INFER: bool = True
    # 历史配置名，保留兼容旧环境变量
    FRAME_DESC_PROBE_VINCI_BEFORE_INFER: bool = True
    FRAME_DESC_PROBE_TIMEOUT_SECONDS: float = 1.5
    # 上下文融合窗口（描述历史条数）
    FRAME_DESC_CONTEXT_WINDOW_SIZE: int = 5
    # 相似度阈值：帧描述文本与上条描述相似度超过此值时跳过推理
    FRAME_DESC_SIMILARITY_THRESHOLD: float = 0.82
    # 场景未变化检测：连续 N 次相似度超过阈值后强制跳过推理
    FRAME_DESC_SCENE_STABLE_THRESHOLD: int = 4
    # 场景稳定时是否直接跳过 description 事件；默认 False，保证前端每次采样都能收到描述
    FRAME_DESC_SKIP_STABLE_SCENE: bool = False
    # 是否开启上下文融合二次推理；默认关闭，优先保障实时性与连接稳定
    FRAME_DESC_ENABLE_CONTEXT_FUSION: bool = False
    # 降级模式下描述频率（秒）：Vinci 不可用时降为低频描述
    FRAME_DESC_DEGRADED_INTERVAL_SECONDS: float = 10.0
    # 降级模式：描述文本固定前缀（可标注"可能"等置信度词汇）
    FRAME_DESC_DEGRADED_PREFIX: str = "（描述服务暂不可用，仅供参考）"
    # 单次推理最大输入帧数
    FRAME_DESC_MAX_FRAMES_PER_REQUEST: int = 3
    # 输入帧最大边长（像素）；超过此值会自动缩放
    FRAME_DESC_MAX_FRAME_SIZE: int = 320
    # Token 预算上限（估算），超预算时自动降频
    FRAME_DESC_TOKEN_BUDGET: int = 6000
    # 是否自动降级（Vinci 不可用时返回降级描述而非错误）
    FRAME_DESC_AUTO_DEGRADE: bool = True
    # 是否使用 Vinci internvl SSE 流式端点；[废弃兼容] 保留给 FRAME_DESC_BACKEND=vinci 场景，默认关闭。
    FRAME_DESC_USE_VINCI_STREAM: bool = False
    # 是否使用 Qwen3-VL SSE 流式端点；CPU 推理首 token 较慢，默认走稳定的非流式端点。
    FRAME_DESC_USE_QWEN3VL_STREAM: bool = False
    # 本地联调云端视频时，允许实时描述接口仅基于前端传入帧推理，不要求 video_id 存在于本地库。
    FRAME_DESC_ALLOW_EXTERNAL_VIDEO: bool = False
    # iOS WKWebView 以 file:// 加载时，云端视频流缺少 CORS 会导致 canvas 采帧失败。
    # 该开关仅允许实时画面描述后端按白名单 URL 抽取单帧，不影响常规视频播放接口。
    FRAME_DESC_ALLOW_SERVER_FRAME_FETCH: bool = False
    FRAME_DESC_SERVER_FRAME_ALLOWED_HOSTS: Union[str, List[str]] = ""
    FRAME_DESC_SERVER_FRAME_FETCH_TIMEOUT_SECONDS: float = 35.0
    # 实时描述链路 DEBUG 日志开关（建议本地联调开启）
    FRAME_DESC_DEBUG_LOG: bool = False
    # 实时描述链路 DEBUG 文件日志；相对路径基于后端仓库根目录。
    FRAME_DESC_DEBUG_LOG_FILE: str = "logs/frame_description_debug.log"
    # 本地 Qwen3-VL 不可用时，是否使用通义千问视觉模型作为 Qwen-family 云端补偿层。
    FRAME_DESC_CLOUD_FALLBACK_ENABLED: bool = False
    FRAME_DESC_CLOUD_PROVIDER: str = "qwen"
    FRAME_DESC_CLOUD_QWEN_MODEL: str = "qwen3-vl-plus"
    FRAME_DESC_CLOUD_QWEN_TIMEOUT_SECONDS: float = 45.0
    FRAME_DESC_CLOUD_QWEN_MAX_TOKENS: int = 256

    # Qwen3-VL 实时画面描述微服务（推荐用于本地 Mac 跑模型，云端后端远程调用）
    QWEN3VL_BASE_URL: str = "http://127.0.0.1:18082"
    QWEN3VL_HEALTH_PATH: str = "/health"
    QWEN3VL_DESCRIBE_PATH: str = "/api/v1/video/describe"
    QWEN3VL_STREAM_PATH: str = "/api/v1/video/describe/stream"
    QWEN3VL_CONNECT_TIMEOUT_SECONDS: float = 2.0
    QWEN3VL_REQUEST_TIMEOUT_SECONDS: float = 8.0
    QWEN3VL_STREAM_TIMEOUT_SECONDS: float = 30.0
    QWEN3VL_MAX_NEW_TOKENS: int = 64

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
    # 关键词搜索增强：允许在排序中引入视频 tags 的词面匹配信号（默认关闭，避免影响线上既有排序）
    SEARCH_TAG_MATCH_ENABLED: bool = False
    SEARCH_TAG_MATCH_WEIGHT: float = 0.18

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
    # 存储维护：定期清理临时/残留文件，控制云端磁盘增长
    STORAGE_MAINTENANCE_ENABLED: bool = True
    STORAGE_MAINTENANCE_INTERVAL_SECONDS: int = 6 * 3600
    STORAGE_MAINTENANCE_FILE_MAX_AGE_HOURS: int = 24
    STORAGE_MAINTENANCE_CHUNK_DIR_MAX_AGE_HOURS: int = 12
    STORAGE_MAINTENANCE_CHROMA_BACKUP_MAX_AGE_HOURS: int = 7 * 24
    STORAGE_MAINTENANCE_KEEP_RECENT_CHROMA_BACKUPS: int = 1

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
    ANALYTICS_ALERT_MAX_P95_LATENCY_MS: float = 12_000.0
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
