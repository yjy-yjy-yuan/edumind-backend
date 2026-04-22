"""视频链接下载后台任务"""

import hashlib
import logging
import os
import re
from typing import Optional
from typing import Tuple

from app.core.config import settings
from app.models.video import Video
from app.models.video import VideoStatus
from app.tasks.video_processing import update_video_status
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

DOWNLOAD_PREPARE_PROGRESS = 5.0
DOWNLOAD_METADATA_PROGRESS = 15.0
DOWNLOAD_RUNNING_PROGRESS = 45.0
DOWNLOAD_VERIFY_PROGRESS = 85.0
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def secure_filename_with_chinese(filename: str) -> str:
    """安全的文件名处理，保留中文字符"""
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
    return safe_name.strip(". ")


def get_video_db_session() -> Tuple[object, Session]:
    """创建任务内独立数据库连接"""
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
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
        options["proxy"] = "http://127.0.0.1:7890"
        options["cookiesfrombrowser"] = ("chrome",)

    return options


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


def finalize_video_record(video_id: int, *, filename: str, filepath: str, title: str, md5: str, model: str = ""):
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
    try:
        import yt_dlp
        from app.tasks.video_processing import process_video_task

        update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_PREPARE_PROGRESS, "准备下载")

        download_folder = settings.UPLOAD_FOLDER
        os.makedirs(download_folder, exist_ok=True)

        update_video_status(video_id, VideoStatus.DOWNLOADING, DOWNLOAD_METADATA_PROGRESS, "获取视频信息")
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

        finalize_video_record(
            video_id,
            filename=os.path.basename(downloaded_path),
            filepath=downloaded_path,
            title=output_title,
            md5=md5,
            model=model or settings.WHISPER_MODEL,
        )
        update_video_status(
            video_id, VideoStatus.PENDING, 0.0, f"下载完成，准备处理（{model or settings.WHISPER_MODEL}）"
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
        mark_download_failed(video_id, str(exc))
