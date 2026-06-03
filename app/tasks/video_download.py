"""视频链接下载后台任务"""

import hashlib
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


def secure_filename_with_chinese(filename: str) -> str:
    """安全的文件名处理，保留中文字符"""
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
    return safe_name.strip(". ")


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
        if proxy:
            options["proxy"] = proxy
        if browser_cookie:
            options["cookiesfrombrowser"] = (browser_cookie,)

    if source_type == "mooc":
        cookie_file = str(getattr(settings, "MOOC_DOWNLOAD_COOKIE_FILE", "") or "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file

    return options


def build_download_error_message(source_type: str, error_message: str) -> str:
    """补充远程平台下载配置提示，避免反爬/网络失败时只暴露 yt-dlp 原始错误。"""
    normalized_error = str(error_message or "").strip()
    source_hints = {
        "youtube": "请检查 YOUTUBE_DOWNLOAD_PROXY 或 YOUTUBE_DOWNLOAD_BROWSER_COOKIE 配置。",
        "mooc": "请检查网络访问和 MOOC_DOWNLOAD_COOKIE_FILE 配置；中国大学慕课课程可能需要登录态，且 yt-dlp 可能不支持该页面。",
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
        with yt_dlp.YoutubeDL(build_ydl_options(download_folder, source_type)) as ydl:
            info = ydl.extract_info(video_url, download=False)

        raw_title = info.get("title") or f"{source_type}-{video_id}"
        safe_title = secure_filename_with_chinese(raw_title) or f"{source_type}-{video_id}"
        output_title = f"{build_source_prefix(source_type)}{safe_title}"
        output_pattern = os.path.join(download_folder, f"{output_title}.%(ext)s")

        update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_RUNNING_PROGRESS, "下载视频")
        with yt_dlp.YoutubeDL(build_ydl_options(download_folder, source_type, output_pattern)) as ydl:
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
            cleaned = cleanup_download_residue(download_folder, output_title)
            if cleaned > 0:
                logger.info(
                    "下载失败后已清理残留临时文件 | video_id=%s | count=%s",
                    video_id,
                    cleaned,
                )
        mark_download_failed(video_id, build_download_error_message(source_type, str(exc)))
