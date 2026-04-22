"""远程视频链接导入服务。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.models.video import Video
from app.models.video import VideoStatus
from app.services.video_content_service import build_subject_enriched_tags
from app.services.video_processing_registry import remember_video_processing_request
from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MOOC_LEARN_RE = re.compile(r"learn/([^-]+)-(\d+)")


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
    is_mooc = "icourse163.org" in normalized_url

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

    if is_youtube:
        video_id = ""
        if "youtube.com" in normalized_url:
            match = re.search(r"v=([^&]+)", normalized_url)
            if match:
                video_id = match.group(1)
        elif "youtu.be" in normalized_url:
            video_id = normalized_url.split("/")[-1].split("?")[0]
        if not video_id:
            raise HTTPException(status_code=400, detail="无效的YouTube视频链接")
        return "youtube", f"youtube-{video_id}"

    if is_mooc:
        course_match = MOOC_LEARN_RE.search(normalized_url)
        if course_match:
            course_id = course_match.group(2)
            return "mooc", f"mooc-{course_id}"
        raise HTTPException(
            status_code=422,
            detail="当前中国大学慕课候选为搜索页或非课程详情页，暂不支持直接入库，请先打开具体课程页。",
        )

    raise HTTPException(status_code=400, detail="目前仅支持B站、YouTube和中国大学慕课视频")


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
