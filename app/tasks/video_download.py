"""视频链接下载后台任务"""

import hashlib
import json
import logging
import os
import re
from typing import Optional, Tuple

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.video import Video, VideoStatus
from app.tasks.video_processing import update_video_status

logger = logging.getLogger(__name__)

DOWNLOAD_PREPARE_PROGRESS = 5.0
DOWNLOAD_METADATA_PROGRESS = 15.0
DOWNLOAD_RUNNING_PROGRESS = 45.0
DOWNLOAD_VERIFY_PROGRESS = 85.0
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
PARTIAL_RESIDUE_SUFFIXES = {".part", ".ytdl", ".tmp", ".temp", ".download"}
YOUTUBE_DEFAULT_FORMAT = "bestvideo*+bestaudio/best"
YOUTUBE_FORBIDDEN_PATTERNS = (
    "http error 403",
    "forbidden",
    "unable to download video data",
)
SENSITIVE_OPTION_KEYS = {
    "cookie",
    "cookies",
    "cookiefile",
    "cookiesfrombrowser",
    "http_headers",
}


def parse_non_negative_int_config(name: str, raw_value: object) -> Optional[int]:
    """解析非负整数配置；非法时记录 warning 并忽略。"""
    if raw_value is None or raw_value == "":
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        logger.warning("%s 必须是非负整数，已忽略 | value=%s", name, raw_value)
        return None
    if value < 0:
        logger.warning("%s 必须是非负整数，已忽略 | value=%s", name, raw_value)
        return None
    return value


def parse_non_negative_float_config(name: str, raw_value: object) -> Optional[float]:
    """解析非负浮点配置；非法时记录 warning 并忽略。"""
    if raw_value is None or raw_value == "":
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        logger.warning("%s 必须是非负数字，已忽略 | value=%s", name, raw_value)
        return None
    if value < 0:
        logger.warning("%s 必须是非负数字，已忽略 | value=%s", name, raw_value)
        return None
    return value


def secure_filename_with_chinese(filename: str) -> str:
    """安全的文件名处理，保留中文字符"""
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
    return safe_name.strip(". ")


def parse_browser_cookie_spec(raw_value: str) -> Optional[tuple]:
    """解析浏览器 Cookie 来源配置，输出 yt-dlp cookiesfrombrowser 参数。"""
    value = str(raw_value or "").strip()
    if not value:
        return None

    browser, _, profile = value.partition(":")
    browser = browser.strip().lower()
    profile = profile.strip()
    supported = {"chrome", "firefox", "edge", "safari"}
    if browser not in supported:
        logger.warning("忽略不支持的 YouTube 浏览器 Cookie 来源 | browser=%s", browser)
        return None
    if profile:
        return (browser, profile)
    return (browser,)


def parse_youtube_extractor_args(raw_value: str) -> Optional[dict]:
    """解析 YouTube extractor args JSON；失败时记录日志并继续使用默认配置。"""
    value = str(raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        logger.warning("YOUTUBE_EXTRACTOR_ARGS 不是合法 JSON，已忽略 | error=%s", exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("YOUTUBE_EXTRACTOR_ARGS 必须是 JSON object，已忽略")
        return None
    return parsed


def is_youtube_forbidden_error(error_message: str) -> bool:
    """判断 YouTube 下载错误是否为 403/反爬下载失败。"""
    normalized = str(error_message or "").lower()
    return any(pattern in normalized for pattern in YOUTUBE_FORBIDDEN_PATTERNS)


def sanitize_ydl_options_for_log(options: dict) -> dict:
    """脱敏 yt-dlp 配置，避免日志泄露 Cookie 或认证头。"""
    sanitized = {}
    for key, value in (options or {}).items():
        if key in SENSITIVE_OPTION_KEYS:
            sanitized[key] = "***redacted***"
        else:
            sanitized[key] = value
    return sanitized


def get_video_db_session() -> Tuple[object, Session]:
    """创建任务内独立数据库连接"""
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        connect_args=({"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}),
    )
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory()


def build_source_prefix(source_type: str) -> str:
    """构建来源前缀"""
    prefix_map = {
        "bilibili": "bilibili-",
        "youtube": "youtube-",
        "mooc": "mooc-",
    }
    return prefix_map.get(source_type, "video-")


def build_ydl_options(download_folder: str, source_type: str, outtmpl: Optional[str] = None) -> dict:
    """构建 yt-dlp 配置"""
    options = {
        "outtmpl": outtmpl or os.path.join(download_folder, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    if source_type == "youtube":
        proxy = str(getattr(settings, "YOUTUBE_DOWNLOAD_PROXY", "") or "").strip()
        browser_cookie = str(getattr(settings, "YOUTUBE_DOWNLOAD_BROWSER_COOKIE", "") or "").strip()
        cookie_file = str(getattr(settings, "YOUTUBE_DOWNLOAD_COOKIE_FILE", "") or "").strip()
        user_agent = str(getattr(settings, "YOUTUBE_DOWNLOAD_USER_AGENT", "") or "").strip()
        referer = str(getattr(settings, "YOUTUBE_DOWNLOAD_REFERER", "") or "").strip()
        requested_format = str(getattr(settings, "YOUTUBE_DOWNLOAD_FORMAT", "") or "").strip()
        impersonate = str(getattr(settings, "YOUTUBE_DOWNLOAD_IMPERSONATE", "") or "").strip()
        sleep_requests = parse_non_negative_float_config(
            "YOUTUBE_DOWNLOAD_SLEEP_REQUESTS",
            getattr(settings, "YOUTUBE_DOWNLOAD_SLEEP_REQUESTS", 0.0),
        )
        retries = parse_non_negative_int_config(
            "YOUTUBE_DOWNLOAD_RETRIES",
            getattr(settings, "YOUTUBE_DOWNLOAD_RETRIES", 10),
        )
        extractor_retries = parse_non_negative_int_config(
            "YOUTUBE_DOWNLOAD_EXTRACTOR_RETRIES",
            getattr(settings, "YOUTUBE_DOWNLOAD_EXTRACTOR_RETRIES", 3),
        )
        extractor_args = parse_youtube_extractor_args(getattr(settings, "YOUTUBE_EXTRACTOR_ARGS", ""))
        if proxy:
            options["proxy"] = proxy
        browser_cookie_spec = parse_browser_cookie_spec(browser_cookie)
        if browser_cookie_spec:
            options["cookiesfrombrowser"] = browser_cookie_spec
        if cookie_file:
            options["cookiefile"] = cookie_file
        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if referer:
            headers["Referer"] = referer
        if headers:
            options["http_headers"] = headers
        options["format"] = requested_format or YOUTUBE_DEFAULT_FORMAT
        if impersonate:
            options["impersonate"] = impersonate
        if sleep_requests is not None:
            options["sleep_interval_requests"] = sleep_requests
        if retries is not None:
            options["retries"] = retries
        if extractor_retries is not None:
            options["extractor_retries"] = extractor_retries
        if extractor_args:
            options["extractor_args"] = extractor_args

    if source_type == "mooc":
        cookie_file = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE_FILE", "") or "").strip()
        cookie_header = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE", "") or "").strip()
        user_agent = str(getattr(settings, "MOOC_DOWNLOAD_USER_AGENT", "") or "").strip()
        referer = str(getattr(settings, "MOOC_DOWNLOAD_REFERER", "") or "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file
        headers = {}
        if cookie_header:
            headers["Cookie"] = cookie_header
        if user_agent:
            headers["User-Agent"] = user_agent
        if referer:
            headers["Referer"] = referer
        if headers:
            options["http_headers"] = headers

    return options


def build_download_error_message(source_type: str, error_message: str) -> str:
    """补充远程平台下载配置提示，避免反爬/网络失败时只暴露 yt-dlp 原始错误。"""
    normalized_error = str(error_message or "").strip()
    if source_type == "youtube" and is_youtube_forbidden_error(normalized_error):
        youtube_hint = (
            "YouTube 下载被平台拒绝，请配置 YOUTUBE_DOWNLOAD_PROXY、"
            "YOUTUBE_DOWNLOAD_BROWSER_COOKIE 或 YOUTUBE_DOWNLOAD_COOKIE_FILE。"
        )
        if youtube_hint in normalized_error:
            return normalized_error
        return f"{normalized_error} {youtube_hint}".strip()

    source_hints = {
        "youtube": "请检查 YOUTUBE_DOWNLOAD_PROXY、YOUTUBE_DOWNLOAD_BROWSER_COOKIE 或 YOUTUBE_DOWNLOAD_COOKIE_FILE 配置。",
        "mooc": (
            "中国大学慕课直导需要 MOOC_DIRECT_IMPORT_ENABLED=true，并配置 "
            "MOOC_DOWNLOAD_COOKIE_FILE 或 MOOC_DOWNLOAD_COOKIE；若课程视频受 DRM/API 限制，"
            "请上传本地视频/音频文件。"
        ),
    }
    hint = source_hints.get(source_type)
    if not hint:
        return normalized_error

    if hint in normalized_error:
        return normalized_error
    return f"{normalized_error} {hint}".strip()


def resolve_downloaded_file_path(download_folder: str, output_title: str) -> str:
    """解析下载完成后的文件路径"""
    prefix = f"{output_title}."
    candidates = []

    for name in os.listdir(download_folder):
        if not name.startswith(prefix):
            continue
        file_path = os.path.join(download_folder, name)
        if os.path.isfile(file_path):
            candidates.append(file_path)

    if not candidates:
        raise FileNotFoundError(f"下载的视频文件不存在: {output_title}")

    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def compute_file_md5(file_path: str) -> str:
    """计算文件 MD5"""
    digest = hashlib.md5()
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_download_residue(download_folder: str, output_title: str) -> int:
    """清理下载过程中可能残留的临时文件（不删除最终视频文件）。"""
    removed = 0
    prefix = f"{output_title}."
    if not os.path.isdir(download_folder):
        return removed

    for name in os.listdir(download_folder):
        if not name.startswith(prefix):
            continue
        suffix = os.path.splitext(name)[1].lower()
        if suffix not in PARTIAL_RESIDUE_SUFFIXES:
            continue
        file_path = os.path.join(download_folder, name)
        if not os.path.isfile(file_path):
            continue
        try:
            os.remove(file_path)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理下载残留失败 | path=%s | error=%s", file_path, exc)
    return removed


def cleanup_failed_download_files(download_folder: str, output_title: str) -> int:
    """下载失败后清理同名前缀残留，包括未完成视频和临时文件。"""
    removed = 0
    prefix = f"{output_title}."
    if not os.path.isdir(download_folder):
        return removed

    for name in os.listdir(download_folder):
        if not name.startswith(prefix):
            continue
        file_path = os.path.join(download_folder, name)
        if not os.path.isfile(file_path):
            continue
        try:
            os.remove(file_path)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理下载失败残留文件失败 | path=%s | error=%s", file_path, exc)
    return removed


def finalize_video_record(
    video_id: int,
    *,
    filename: str,
    filepath: str,
    title: str,
    md5: str,
    model: str = "",
):
    """写回下载完成的视频记录"""
    engine, db = get_video_db_session()
    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            logger.warning("视频记录不存在，无法完成下载写回: %s", video_id)
            return

        video.filename = filename
        video.filepath = filepath
        video.title = title
        video.md5 = md5
        video.status = VideoStatus.UPLOADED
        video.process_progress = 0.0
        video.current_step = f"已上传，等待处理（{model}）" if str(model or "").strip() else "已上传，等待处理"
        video.error_message = None
        db.commit()
    finally:
        db.close()
        engine.dispose()


def mark_download_failed(video_id: int, error_message: str):
    """标记下载失败"""
    update_video_status(
        video_id,
        VideoStatus.FAILED,
        0.0,
        "下载失败",
        error_message=error_message[:1000],
    )


def mooc_download_configured() -> bool:
    """判断中国大学慕课下载任务是否允许进入实验直导链路。"""
    if not bool(getattr(settings, "MOOC_DIRECT_IMPORT_ENABLED", False)):
        return False
    cookie_file = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE_FILE", "") or "").strip()
    cookie_header = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE", "") or "").strip()
    return bool(cookie_file or cookie_header)


def _download_mooc_video(
    video_id: int,
    video_url: str,
    download_folder: str,
    *,
    auto_generate_summary: bool = True,
    auto_generate_tags: bool = True,
    summary_style: str = "study",
    model: Optional[str] = None,
    language: str = "zh",
) -> None:
    """通过 icourse163 专用解析器执行实验性慕课视频下载。"""
    from app.services.video.icourse163_parser import (
        normalize_mooc_parser_error,
        parse_and_download_mooc,
    )
    from app.tasks.video_processing import process_video_task

    if not mooc_download_configured():
        raise RuntimeError(build_download_error_message("mooc", "unsupported_direct_import"))

    os.makedirs(download_folder, exist_ok=True)
    update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_PREPARE_PROGRESS, "准备下载慕课视频")

    cookie_file = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE_FILE", "") or "").strip()
    cookie_header = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE", "") or "").strip()
    user_agent = str(getattr(settings, "MOOC_DOWNLOAD_USER_AGENT", "") or "").strip()
    referer = str(getattr(settings, "MOOC_DOWNLOAD_REFERER", "") or "").strip()

    update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_METADATA_PROGRESS, "解析慕课视频信息")
    try:
        downloaded_path, video_title = parse_and_download_mooc(
            url=video_url,
            output_dir=download_folder,
            cookie_file=cookie_file,
            cookie_header=cookie_header,
            user_agent=user_agent,
            referer=referer,
        )
    except Exception as exc:  # noqa: BLE001
        normalized_error = normalize_mooc_parser_error(exc)
        if getattr(normalized_error, "debug_detail", ""):
            logger.error(
                "中国大学慕课解析失败 | video_id=%s | code=%s | detail=%s",
                video_id,
                getattr(normalized_error, "code", "mooc_parser_error"),
                normalized_error.debug_detail,
            )
        raise RuntimeError(build_download_error_message("mooc", str(normalized_error))) from exc

    safe_title = secure_filename_with_chinese(video_title) or f"mooc-{video_id}"
    output_title = f"{build_source_prefix('mooc')}{safe_title}"

    update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_VERIFY_PROGRESS, "校验视频文件")
    md5 = compute_file_md5(downloaded_path)
    finalize_video_record(
        video_id,
        filename=os.path.basename(downloaded_path),
        filepath=downloaded_path,
        title=output_title,
        md5=md5,
        model=model or settings.WHISPER_MODEL,
    )
    update_video_status(
        video_id,
        VideoStatus.PENDING,
        0.0,
        f"下载完成，准备处理（{model or settings.WHISPER_MODEL}）",
    )
    process_video_task(
        video_id,
        language,
        model or settings.WHISPER_MODEL,
        auto_generate_summary=auto_generate_summary,
        auto_generate_tags=auto_generate_tags,
        summary_style=summary_style,
    )
    logger.info("慕课视频下载完成: id=%s path=%s", video_id, downloaded_path)


def download_video_from_url_task(
    video_id: int,
    video_url: str,
    source_type: str,
    *,
    auto_generate_summary: bool = True,
    auto_generate_tags: bool = True,
    summary_style: str = "study",
    model: Optional[str] = None,
    language: str = "zh",
):
    """下载远程视频到本地上传目录"""
    download_folder = settings.UPLOAD_FOLDER
    output_title = None
    try:
        if source_type == "mooc":
            return _download_mooc_video(
                video_id,
                video_url,
                download_folder,
                auto_generate_summary=auto_generate_summary,
                auto_generate_tags=auto_generate_tags,
                summary_style=summary_style,
                model=model,
                language=language,
            )

        import yt_dlp

        from app.tasks.video_processing import process_video_task

        update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_PREPARE_PROGRESS, "准备下载")

        os.makedirs(download_folder, exist_ok=True)

        update_video_status(
            video_id,
            VideoStatus.DOWNLOADING,
            DOWNLOAD_METADATA_PROGRESS,
            "获取视频信息",
        )
        metadata_options = build_ydl_options(download_folder, source_type)
        logger.debug(
            "远程视频元信息解析配置 | source=%s | options=%s",
            source_type,
            sanitize_ydl_options_for_log(metadata_options),
        )
        with yt_dlp.YoutubeDL(metadata_options) as ydl:
            info = ydl.extract_info(video_url, download=False)

        raw_title = info.get("title") or f"{source_type}-{video_id}"
        safe_title = secure_filename_with_chinese(raw_title) or f"{source_type}-{video_id}"
        output_title = f"{build_source_prefix(source_type)}{safe_title}"
        output_pattern = os.path.join(download_folder, f"{output_title}.%(ext)s")

        update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_RUNNING_PROGRESS, "下载视频")
        download_options = build_ydl_options(download_folder, source_type, output_pattern)
        logger.debug(
            "远程视频下载配置 | source=%s | options=%s", source_type, sanitize_ydl_options_for_log(download_options)
        )
        with yt_dlp.YoutubeDL(download_options) as ydl:
            ydl.download([video_url])

        update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_VERIFY_PROGRESS, "校验视频文件")
        downloaded_path = resolve_downloaded_file_path(download_folder, output_title)
        md5 = compute_file_md5(downloaded_path)
        cleaned = cleanup_download_residue(download_folder, output_title)
        if cleaned > 0:
            logger.info(
                "下载完成后已清理残留临时文件 | video_id=%s | count=%s",
                video_id,
                cleaned,
            )

        finalize_video_record(
            video_id,
            filename=os.path.basename(downloaded_path),
            filepath=downloaded_path,
            title=output_title,
            md5=md5,
            model=model or settings.WHISPER_MODEL,
        )
        update_video_status(
            video_id,
            VideoStatus.PENDING,
            0.0,
            f"下载完成，准备处理（{model or settings.WHISPER_MODEL}）",
        )
        process_video_task(
            video_id,
            language,
            model or settings.WHISPER_MODEL,
            auto_generate_summary=auto_generate_summary,
            auto_generate_tags=auto_generate_tags,
            summary_style=summary_style,
        )
        logger.info("链接视频下载完成: id=%s path=%s", video_id, downloaded_path)
    except Exception as exc:
        logger.error("链接视频下载失败: id=%s error=%s", video_id, exc)
        if output_title:
            cleaned = cleanup_failed_download_files(download_folder, output_title)
            if cleaned > 0:
                logger.info(
                    "下载失败后已清理残留临时文件 | video_id=%s | count=%s",
                    video_id,
                    cleaned,
                )
        mark_download_failed(video_id, build_download_error_message(source_type, str(exc)))
