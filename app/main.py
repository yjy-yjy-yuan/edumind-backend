"""FastAPI 应用入口"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.base import Base
from app.models.recommendation_ops_event import RecommendationOpsEvent  # noqa: F401
from app.models.semantic_search_log import SemanticSearchLog  # noqa: F401
from app.models.video import Video, VideoStatus
from app.services.ollama_runtime import get_ollama_runtime_status
from app.services.similarity_service_container import init_persistence_service
from app.services.storage_maintenance import (
    run_storage_maintenance_once,
    start_storage_maintenance_worker,
    stop_storage_maintenance_worker,
)
from app.services.whisper_runtime import (
    get_whisper_runtime_status,
    shutdown_whisper_runtime,
    start_whisper_background_preload,
)
from app.utils.frame_description_debug import get_frame_description_debug_logger

# 配置日志
LOG_LEVEL = logging.DEBUG if settings.DEBUG else logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)
logging.getLogger("uvicorn").setLevel(LOG_LEVEL)
logging.getLogger("uvicorn.error").setLevel(LOG_LEVEL)
logging.getLogger("uvicorn.access").setLevel(LOG_LEVEL)
logger = logging.getLogger(__name__)


def configure_frame_desc_debug_logging() -> str:
    """Configure the dedicated DEBUG file logger for realtime frame description."""
    raw_path = str(
        getattr(settings, "FRAME_DESC_DEBUG_LOG_FILE", "logs/frame_description_debug.log")
        or "logs/frame_description_debug.log"
    ).strip()
    log_path = Path(raw_path)
    if not log_path.is_absolute():
        log_path = Path(settings.BASE_DIR) / log_path
    get_frame_description_debug_logger()
    return str(log_path)


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = perf_counter()
        response = await call_next(request)
        elapsed_ms = (perf_counter() - start) * 1000
        status_code = int(getattr(response, "status_code", 0) or 0)
        log_message = "request completed | method=%s | path=%s | status=%s | elapsed_ms=%.2f"
        log_args = (request.method, request.url.path, status_code, elapsed_ms)
        if status_code >= 500:
            logger.error(log_message, *log_args)
        elif status_code >= 400:
            logger.warning(log_message, *log_args)
        else:
            logger.info(log_message, *log_args)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if str(settings.APP_ENV).lower() == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def recover_interrupted_video_tasks():
    """将服务重启前中断的后台任务转为失败，避免状态永久卡住。"""
    db = SessionLocal()
    try:
        interrupted_statuses = [
            VideoStatus.PENDING,
            VideoStatus.PROCESSING,
            VideoStatus.DOWNLOADING,
        ]
        interrupted_videos = db.query(Video).filter(Video.status.in_(interrupted_statuses)).all()
        if not interrupted_videos:
            return

        for video in interrupted_videos:
            previous_status = video.status
            video.status = VideoStatus.FAILED
            video.process_progress = 0.0
            video.error_message = "服务重启后检测到后台任务已中断，请重新提交处理。"
            if previous_status == VideoStatus.DOWNLOADING:
                video.current_step = "下载任务已中断，请重新提交"
            else:
                video.current_step = "处理任务已中断，请重新提交"

        db.commit()
        logger.warning("已恢复中断的视频任务 | count=%s", len(interrupted_videos))
    except Exception as exc:
        db.rollback()
        logger.error("恢复中断的视频任务失败 | error=%s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    storage_maintenance_worker = None
    storage_maintenance_stop_event = None
    # 启动时执行
    logger.info("启动 %s API...", settings.APP_NAME)
    if bool(getattr(settings, "FRAME_DESC_DEBUG_LOG", False)):
        debug_log_file = configure_frame_desc_debug_logging()
        get_frame_description_debug_logger().debug(
            "startup config | enabled=%s | backend=%s | qwen3vl_base=%s | qwen3vl_describe_path=%s | qwen3vl_request_timeout=%s | qwen3vl_stream_timeout=%s | frame_max_size=%s | vinci_base=%s | qwen3vl_stream=%s | vinci_stream=%s | allow_external_video=%s | allow_server_frame_fetch=%s | server_frame_hosts=%s | probe=%s | probe_timeout=%s | auto_degrade=%s | cloud_fallback_enabled=%s | cloud_provider=%s | cloud_qwen_model=%s | debug_log_file=%s",
            getattr(settings, "FRAME_DESC_ENABLED", None),
            getattr(settings, "FRAME_DESC_BACKEND", None),
            getattr(settings, "QWEN3VL_BASE_URL", None),
            getattr(settings, "QWEN3VL_DESCRIBE_PATH", None),
            getattr(settings, "QWEN3VL_REQUEST_TIMEOUT_SECONDS", None),
            getattr(settings, "QWEN3VL_STREAM_TIMEOUT_SECONDS", None),
            getattr(settings, "FRAME_DESC_MAX_FRAME_SIZE", None),
            getattr(settings, "VINCI_BASE_URL", None),
            getattr(settings, "FRAME_DESC_USE_QWEN3VL_STREAM", None),
            getattr(settings, "FRAME_DESC_USE_VINCI_STREAM", None),
            getattr(settings, "FRAME_DESC_ALLOW_EXTERNAL_VIDEO", None),
            getattr(settings, "FRAME_DESC_ALLOW_SERVER_FRAME_FETCH", None),
            getattr(settings, "FRAME_DESC_SERVER_FRAME_ALLOWED_HOSTS", None),
            getattr(settings, "FRAME_DESC_PROBE_UPSTREAM_BEFORE_INFER", None),
            getattr(settings, "FRAME_DESC_PROBE_TIMEOUT_SECONDS", None),
            getattr(settings, "FRAME_DESC_AUTO_DEGRADE", None),
            getattr(settings, "FRAME_DESC_CLOUD_FALLBACK_ENABLED", None),
            getattr(settings, "FRAME_DESC_CLOUD_PROVIDER", None),
            getattr(settings, "FRAME_DESC_CLOUD_QWEN_MODEL", None),
            debug_log_file,
        )

    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
        logger.info("数据库表创建成功")
    else:
        logger.info("已跳过自动建表；如需初始化数据库，请运行 backend_fastapi/scripts/init_db.py")

    recover_interrupted_video_tasks()

    init_persistence_service()

    # 确保上传目录存在
    os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(settings.SUBTITLE_FOLDER, exist_ok=True)
    os.makedirs(settings.PREVIEW_FOLDER, exist_ok=True)
    os.makedirs(settings.TEMP_FOLDER, exist_ok=True)
    logger.info(f"上传目录已就绪: {settings.UPLOAD_FOLDER}")
    cors_safe = [o for o in settings.CORS_ORIGINS if o not in ("*", "null", None, "")]
    logger.info(
        "CORS 允许来源（已过滤敏感项）: %s | 全量原始值请查看 settings.CORS_ORIGINS",
        cors_safe,
    )

    if settings.STORAGE_MAINTENANCE_ENABLED:
        try:
            run_storage_maintenance_once(
                base_dir=Path(settings.BASE_DIR),
                file_max_age_hours=settings.STORAGE_MAINTENANCE_FILE_MAX_AGE_HOURS,
                chunk_dir_max_age_hours=settings.STORAGE_MAINTENANCE_CHUNK_DIR_MAX_AGE_HOURS,
                chroma_backup_max_age_hours=settings.STORAGE_MAINTENANCE_CHROMA_BACKUP_MAX_AGE_HOURS,
                keep_recent_chroma_backups=settings.STORAGE_MAINTENANCE_KEEP_RECENT_CHROMA_BACKUPS,
            )
            storage_maintenance_worker, storage_maintenance_stop_event = start_storage_maintenance_worker(
                base_dir=Path(settings.BASE_DIR),
                interval_seconds=settings.STORAGE_MAINTENANCE_INTERVAL_SECONDS,
                file_max_age_hours=settings.STORAGE_MAINTENANCE_FILE_MAX_AGE_HOURS,
                chunk_dir_max_age_hours=settings.STORAGE_MAINTENANCE_CHUNK_DIR_MAX_AGE_HOURS,
                chroma_backup_max_age_hours=settings.STORAGE_MAINTENANCE_CHROMA_BACKUP_MAX_AGE_HOURS,
                keep_recent_chroma_backups=settings.STORAGE_MAINTENANCE_KEEP_RECENT_CHROMA_BACKUPS,
            )
            logger.info(
                "已启用存储维护任务 | interval=%ss | max_age=%sh",
                settings.STORAGE_MAINTENANCE_INTERVAL_SECONDS,
                settings.STORAGE_MAINTENANCE_FILE_MAX_AGE_HOURS,
            )
        except Exception as exc:
            logger.warning("初始化存储维护任务失败（忽略，不阻断启动）| error=%s", exc)

    start_whisper_background_preload(settings.WHISPER_MODEL, settings.WHISPER_MODEL_PATH)

    yield

    # 关闭时执行
    if storage_maintenance_worker is not None and storage_maintenance_stop_event is not None:
        stop_storage_maintenance_worker(storage_maintenance_worker, storage_maintenance_stop_event)
    shutdown_whisper_runtime()
    logger.info("关闭 %s API...", settings.APP_NAME)


# 创建 FastAPI 应用
app = FastAPI(
    title=settings.APP_NAME,
    description="基于深度学习的视频智能伴学系统 API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",  # Swagger UI
    redoc_url=None,  # 禁用默认 ReDoc，使用自定义路由
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id", "X-Request-Id"],
)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


# 注册路由
from app.routers import (
    agent,
    auth,
    chat,
    design,
    frame_description,
    note,
    ops,
    qa,
    recommendation,
    search,
    subtitle,
    video,
)

app.include_router(video.router, prefix="/api/videos", tags=["视频管理"])
# 兼容旧客户端历史路径（/api/video/*）
app.include_router(video.router, prefix="/api/video", tags=["视频管理(兼容)"])
app.include_router(subtitle.router, prefix="/api/subtitles", tags=["字幕管理"])
app.include_router(note.router, prefix="/api/notes", tags=["笔记管理"])
app.include_router(qa.router, prefix="/api/qa", tags=["问答系统"])
app.include_router(chat.router, prefix="/api/chat", tags=["聊天系统"])
app.include_router(design.router, prefix="/api/design", tags=["设计助手"])
app.include_router(auth.router, prefix="/api/auth", tags=["用户认证"])
app.include_router(ops.router, prefix="/api/ops", tags=["运维观测"])
app.include_router(recommendation.router, prefix="/api/recommendations", tags=["视频推荐"])
app.include_router(search.router, tags=["语义搜索"])
app.include_router(agent.router, prefix="/api/agent", tags=["学习流智能体"])
app.include_router(frame_description.router, prefix="/api/frame_description", tags=["实时画面描述"])


# 根路由
@app.get("/")
async def root():
    return {
        "status": "success",
        "message": f"Welcome to {settings.APP_NAME} API",
        "version": "2.0.0",
        "docs": "/docs",
    }


# 健康检查
@app.get("/health")
@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "services": {
            "database": "connected",
            "whisper": get_whisper_runtime_status(),
            "ollama": get_ollama_runtime_status(),
        },
    }
