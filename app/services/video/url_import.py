"""远程视频链接导入服务。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.video import Video, VideoStatus
from app.services.video.content import build_subject_enriched_tags
from app.services.video.processing_registry import remember_video_processing_request

logger = logging.getLogger(__name__)

DISABLED_REMOTE_VIDEO_SOURCE_MESSAGE = "暂不支持通过链接上传 YouTube 或中国大学慕课视频，请使用本地视频上传。"
MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE = (
    "当前暂不支持中国大学慕课课程页直接视频处理；需要后续实现 icourse163 专用解析器，" "或由用户上传本地视频/音频文件。"
)


@dataclass
class VideoURLImportResult:
    """远程链接导入结果。"""

    video: Video
    duplicate: bool
    status: str
    message: str


def normalize_prefilled_tags(*, title: str, summary: str, tags: Optional[list[str]]) -> Optional[str]:
    """归一化推荐候选预填标签，便于下载前先写回视频记录。"""
    normalized_tags = build_subject_enriched_tags(tags or [], title=title, summary=summary, max_tags=8)
    if not normalized_tags:
        return None
    return json.dumps(normalized_tags, ensure_ascii=False)


def detect_remote_video_source(video_url: str) -> tuple[str, str]:
    """识别远程视频来源和占位标题。"""
    normalized_url = str(video_url or "").strip()
    is_bilibili = "bilibili.com" in normalized_url or "b23.tv" in normalized_url
    is_youtube = "youtube.com" in normalized_url or "youtu.be" in normalized_url
    is_mooc = is_mooc_video_url(normalized_url)

    if is_youtube:
        video_id = ""
        watch_match = re.search(r"[?&]v=([0-9A-Za-z_-]+)", normalized_url)
        short_match = re.search(r"youtu\.be/([0-9A-Za-z_-]+)", normalized_url)
        if watch_match:
            video_id = watch_match.group(1)
        elif short_match:
            video_id = short_match.group(1)
        return "youtube", f"youtube-{video_id or 'remote-video'}"

    if is_mooc:
        course_match = re.search(r"icourse163\.org/learn/([^/?#]+)", normalized_url)
        course_id = course_match.group(1) if course_match else "remote-course"
        return "mooc", f"mooc-{course_id}"

    if is_bilibili:
        bv_match = re.search(r"BV[0-9A-Za-z]+", normalized_url)
        av_match = re.search(r"av\d+", normalized_url.lower())
        if bv_match:
            video_id = bv_match.group(0)
            return "bilibili", f"bilibili-{video_id}"
        if av_match:
            video_id = av_match.group(0)
            return "bilibili", f"bilibili-{video_id}"
        raise HTTPException(status_code=400, detail="无效的B站视频链接")

    raise HTTPException(status_code=400, detail="目前仅支持B站、YouTube 和中国大学慕课视频链接")


def is_mooc_video_url(video_url: str) -> bool:
    """识别中国大学慕课 URL。"""
    normalized_url = str(video_url or "").strip().lower()
    return any(
        host in normalized_url
        for host in (
            "icourse163.org",
            "www.icourse163.org",
            "study.icourse163.org",
        )
    )


def reject_mooc_direct_import(video_url: str) -> None:
    """中国大学慕课当前仅可作为推荐候选，不进入直导下载队列。"""
    if not is_mooc_video_url(video_url):
        return
    raise HTTPException(status_code=422, detail=MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE)


def find_existing_remote_video(db: Session, video_url: str, user_id: int) -> Optional[Video]:
    """查找当前用户下同 URL 的现有视频记录（跨用户不复用）。"""
    return (
        db.query(Video)
        .filter(
            Video.url == video_url,
            Video.user_id == user_id,
            Video.status != VideoStatus.FAILED,
        )
        .order_by(Video.upload_time.desc())
        .first()
    )


def create_remote_video_record(
    db: Session,
    *,
    user_id: int,
    video_url: str,
    placeholder_title: str,
    preferred_title: str = "",
    preferred_summary: str = "",
    preferred_tags: Optional[list[str]] = None,
) -> Video:
    """创建下载中的远程视频记录。"""
    record_title = str(preferred_title or "").strip() or placeholder_title
    record_summary = str(preferred_summary or "").strip() or None
    record_tags = normalize_prefilled_tags(title=record_title, summary=record_summary or "", tags=preferred_tags)
    video = Video(
        user_id=user_id,
        title=record_title,
        url=video_url,
        status=VideoStatus.DOWNLOADING,
        process_progress=0.0,
        current_step="已提交，等待下载",
        summary=record_summary,
        tags=record_tags,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def submit_remote_video_download(
    db: Session,
    *,
    video: Video,
    video_url: str,
    source_type: str,
    process_options: dict,
    request_source: str,
) -> None:
    """提交远程视频下载任务。"""
    from app.core.executor import submit_task
    from app.tasks.video_download import download_video_from_url_task

    video.current_step = f"已提交，等待下载（{process_options['model']}）"
    db.commit()
    db.refresh(video)
    submit_task(download_video_from_url_task, video.id, video_url, source_type, **process_options)
    remember_video_processing_request(
        video.id,
        model=process_options["model"],
        language=process_options["language"],
        source=request_source,
    )


def mark_remote_video_submit_failed(db: Session, video: Video, error_message: str) -> None:
    """将提交失败的视频记录写回失败状态。"""
    video.status = VideoStatus.FAILED
    video.current_step = "下载任务提交失败"
    video.error_message = str(error_message or "")[:1000]
    db.commit()


def import_remote_video_from_url(
    db: Session,
    *,
    user_id: int,
    video_url: str,
    process_options: dict,
    preferred_title: str = "",
    preferred_summary: str = "",
    preferred_tags: Optional[list[str]] = None,
    request_source: str = "upload_video_url",
) -> VideoURLImportResult:
    """通过共享导入链路提交远程视频下载并入库。"""
    normalized_url = str(video_url or "").strip()
    reject_mooc_direct_import(normalized_url)
    source_type, placeholder_title = detect_remote_video_source(normalized_url)
    existing_video = find_existing_remote_video(db, normalized_url, user_id)
    if existing_video:
        status = existing_video.status.value if hasattr(existing_video.status, "value") else str(existing_video.status)
        return VideoURLImportResult(
            video=existing_video,
            duplicate=True,
            status=status,
            message="该视频链接已提交过",
        )

    video = create_remote_video_record(
        db,
        user_id=user_id,
        video_url=normalized_url,
        placeholder_title=placeholder_title,
        preferred_title=preferred_title,
        preferred_summary=preferred_summary,
        preferred_tags=preferred_tags,
    )

    try:
        submit_remote_video_download(
            db,
            video=video,
            video_url=normalized_url,
            source_type=source_type,
            process_options=process_options,
            request_source=request_source,
        )
    except Exception as exc:
        logger.error("提交远程视频下载任务失败 | url=%s | error=%s", normalized_url, exc)
        mark_remote_video_submit_failed(db, video, str(exc))
        raise HTTPException(status_code=500, detail="提交链接下载任务失败，请稍后重试") from exc

    return VideoURLImportResult(
        video=video,
        duplicate=False,
        status="downloading",
        message="链接已提交，正在后台下载，下载完成后可自动开始处理",
    )
