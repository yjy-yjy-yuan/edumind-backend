"""后台任务执行器。

本地开发默认使用 ThreadPoolExecutor，避免 macOS + uvicorn reload
下 ProcessPoolExecutor 出现任务提交成功但实际不执行的假死。
"""

import atexit
import logging
from concurrent.futures import Executor
from concurrent.futures import Future
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_executor: Optional[Executor] = None
_executor_kind: Optional[str] = None
_executor_lock = Lock()
_running_tasks: Dict[int, Future] = {}


def _resolve_executor_kind() -> str:
    configured = str(getattr(settings, "BACKGROUND_TASK_EXECUTOR", "auto") or "auto").strip().lower()
    if configured in {"thread", "process"}:
        return configured
    if settings.DEBUG or str(settings.APP_ENV).lower() in {"local", "development"}:
        return "thread"
    return "process"


def _create_executor(kind: str) -> Executor:
    max_workers = max(1, int(getattr(settings, "BACKGROUND_TASK_WORKERS", 2) or 2))
    if kind == "process":
        return ProcessPoolExecutor(max_workers=max_workers)
    return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="edumind-bg")


def get_executor() -> Executor:
    """获取全局后台执行器实例。"""
    global _executor, _executor_kind

    with _executor_lock:
        if _executor is None:
            _executor_kind = _resolve_executor_kind()
            _executor = _create_executor(_executor_kind)
            logger.info(
                "后台执行器初始化完成 | type=%s | max_workers=%s", _executor_kind, settings.BACKGROUND_TASK_WORKERS
            )
            atexit.register(shutdown_executor)
    return _executor


def shutdown_executor():
    """关闭后台执行器。"""
    global _executor, _executor_kind

    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
            # 不在此处写日志：atexit/pytest 收尾时 stderr 可能已关闭，logging 会打出 Logging error 噪声
            _executor = None
            _executor_kind = None
            _running_tasks.clear()


def _safe_mark_task_failed(task_name: str, task_args: tuple[Any, ...], exc: BaseException):
    if not task_args:
        return

    first_arg = task_args[0]
    if not isinstance(first_arg, int):
        return

    if task_name == "cleanup_video_task":
        return

    try:
        from app.models.video import VideoStatus
        from app.tasks.video_processing import update_video_status

        video_id = first_arg
        step = "后台任务失败"
        if task_name == "download_video_from_url_task":
            step = "下载任务失败"
        elif task_name == "process_video_task":
            step = "视频处理任务失败"

        update_video_status(
            video_id,
            VideoStatus.FAILED,
            0.0,
            step,
            error_message=str(exc)[:1000] if str(exc).strip() else step,
        )
    except Exception as mark_exc:
        logger.error("回写后台任务失败状态时出错 | task=%s | error=%s", task_name, mark_exc)


def _handle_future_done(task_name: str, task_args: tuple[Any, ...], future_key: int, future: Future):
    _running_tasks.pop(future_key, None)

    if future.cancelled():
        logger.warning("后台任务被取消 | task=%s", task_name)
        return

    exc = future.exception()
    if exc is None:
        logger.info("后台任务完成 | task=%s", task_name)
        return

    logger.error(
        "后台任务异常 | task=%s | error=%s",
        task_name,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    _safe_mark_task_failed(task_name, task_args, exc)


def submit_task(task_func: Callable, *args, **kwargs) -> Future:
    """提交后台任务。"""
    executor = get_executor()
    future = executor.submit(task_func, *args, **kwargs)
    future_key = id(future)
    _running_tasks[future_key] = future
    future.add_done_callback(lambda done: _handle_future_done(task_func.__name__, args, future_key, done))
    logger.info("后台任务已提交 | task=%s | executor=%s", task_func.__name__, _executor_kind or "unknown")
    return future


def get_task_status(video_id: int) -> dict:
    """获取任务状态（从数据库读取）。"""
    from app.core.database import SessionLocal
    from app.models.video import Video

    db = SessionLocal()
    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        if video:
            return {
                "status": video.status.value if hasattr(video.status, "value") else video.status,
                "progress": video.process_progress,
                "current_step": video.current_step,
            }
        return {"status": "not_found", "progress": 0, "current_step": ""}
    finally:
        db.close()
