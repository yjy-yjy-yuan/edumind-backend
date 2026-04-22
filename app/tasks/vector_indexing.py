"""向量索引后台任务"""

import logging
import os
from datetime import datetime
from typing import Optional

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.vector_index import VectorIndex
from app.models.vector_index import VectorIndexStatus
from app.models.video import Video
from app.models.video import VideoStatus

logger = logging.getLogger(__name__)


def _missing_assets_error(
    video_id: int, *, video_path: Optional[str], subtitle_path: Optional[str], status: str
) -> str:
    """统一构造索引失败的可观测错误信息。"""
    return (
        f"Index source unavailable | video_id={video_id} | status={status} | "
        f"video_path={video_path or 'None'} | subtitle_path={subtitle_path or 'None'}"
    )[:500]


def index_video_for_search(video_id: int, user_id: int, embedding_backend: str = None):
    """
    后台任务：为视频构建语义索引

    流程：
    1. 从 Video 获取视频文件路径
    2. 验证视频处理是否完成
    3. 使用 chunker 分割视频
    4. 使用 embedder 生成向量
    5. 使用 store 存储到 ChromaDB
    6. 更新 VectorIndex 表记录状态

    Args:
        video_id: 视频 ID
        user_id: 用户 ID
        embedding_backend: 嵌入后端（gemini 或 local）
    """
    db = SessionLocal()
    vector_index = None
    video = None
    try:
        backend = embedding_backend or settings.SEARCH_BACKEND

        logger.info(f"Starting background indexing task: video={video_id}, user={user_id}")

        # 获取或创建 VectorIndex 记录
        vector_index = (
            db.query(VectorIndex).filter(VectorIndex.video_id == video_id, VectorIndex.user_id == user_id).first()
        )

        if vector_index is None:
            collection_name = f"user_{user_id}_video_{video_id}_chunks"
            vector_index = VectorIndex(
                video_id=video_id,
                user_id=user_id,
                collection_name=collection_name,
                embedding_backend=backend,
                embedding_model=settings.SEARCH_LOCAL_MODEL if backend == "local" else None,
                status=VectorIndexStatus.PROCESSING,
            )
            db.add(vector_index)
            db.commit()
        else:
            vector_index.status = VectorIndexStatus.PROCESSING
            db.commit()

        # 获取视频信息
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            logger.error(f"Video not found: {video_id}")
            if vector_index:
                vector_index.status = VectorIndexStatus.FAILED
                vector_index.error_message = "Video not found"
                db.commit()
            return

        subtitle_available = bool(video.subtitle_filepath and os.path.exists(video.subtitle_filepath))

        # 手动索引默认要求视频处理完成；但若字幕可用，则允许降级为“字幕索引”继续执行。
        if video.status != VideoStatus.COMPLETED and not subtitle_available:
            logger.warning(f"Video not fully processed: {video_id}, status={video.status}")
            if vector_index:
                vector_index.status = VectorIndexStatus.FAILED
                vector_index.error_message = f"Video processing incomplete: {video.status}"
            video.has_semantic_index = False
            video.vector_index_id = None
            db.commit()
            return

        # 优先使用处理后文件，若没有则回退到原始上传文件。
        # 若视频文件缺失但字幕存在，使用稳定的逻辑 source 标识继续构建字幕索引。
        video_source_path = video.processed_filepath or video.filepath
        has_video_file = bool(video_source_path and os.path.exists(video_source_path))
        if not has_video_file and subtitle_available:
            logger.warning("Video file missing for video %s, fallback to subtitle-only indexing.", video_id)
            video_source_path = f"video://{user_id}/{video_id}"
            has_video_file = True

        if not has_video_file:
            logger.error(f"No available video source for indexing: video_id={video_id}")
            if vector_index:
                vector_index.status = VectorIndexStatus.FAILED
                vector_index.error_message = _missing_assets_error(
                    video_id,
                    video_path=video_source_path,
                    subtitle_path=video.subtitle_filepath,
                    status=str(video.status),
                )
            video.has_semantic_index = False
            video.vector_index_id = None
            db.commit()
            return

        # 索引处理逻辑
        from app.services.search.search import build_video_index_internal

        chunk_count = build_video_index_internal(
            video_id=video_id,
            user_id=user_id,
            video_path=video_source_path,
            collection_name=vector_index.collection_name,
            backend=backend,
            db=db,
            subtitle_path=video.subtitle_filepath,
        )

        # 更新记录
        vector_index.chunk_count = chunk_count
        vector_index.status = VectorIndexStatus.COMPLETED
        vector_index.indexed_at = datetime.utcnow()
        vector_index.error_message = None

        video.has_semantic_index = True
        video.vector_index_id = vector_index.id

        db.commit()
        logger.info(f"Successfully indexed video {video_id}: {chunk_count} chunks")

    except Exception as e:
        logger.error(f"Semantic indexing failed for video {video_id}: {e}", exc_info=True)
        has_updates = False
        if vector_index:
            vector_index.status = VectorIndexStatus.FAILED
            vector_index.error_message = str(e)[:500]  # 限制错误信息长度
            has_updates = True
        if video:
            video.has_semantic_index = False
            video.vector_index_id = None
            has_updates = True
        if has_updates:
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to persist indexing failure state | video=%s user=%s",
                    video_id,
                    user_id,
                )
    finally:
        db.close()


def index_video_inline(video_id: int, user_id: int, subtitle_path: str, embedding_backend: str = None):
    """
    内嵌索引后台任务：在视频处理流程中启动，允许在字幕已就绪时立即进行索引

    与 index_video_for_search 的主要区别：
    - 不要求 video.status == COMPLETED，允许在 PROCESSING 或其他阶段执行
    - 接收已生成的 subtitle_path，避免重复读取字幕
    - 与主处理流程并行执行，降低用户感知的总处理时间

    Args:
        video_id: 视频 ID
        user_id: 用户 ID
        subtitle_path: 已生成的字幕文件路径（SRT 格式）
        embedding_backend: 嵌入后端（gemini 或 local）
    """
    db = SessionLocal()
    vector_index = None
    video = None
    try:
        backend = embedding_backend or settings.SEARCH_BACKEND

        logger.info(f"Starting inline indexing task: video={video_id}, user={user_id}, backend={backend}")

        # 获取或创建 VectorIndex 记录
        vector_index = (
            db.query(VectorIndex).filter(VectorIndex.video_id == video_id, VectorIndex.user_id == user_id).first()
        )

        if vector_index is None:
            collection_name = f"user_{user_id}_video_{video_id}_chunks"
            vector_index = VectorIndex(
                video_id=video_id,
                user_id=user_id,
                collection_name=collection_name,
                embedding_backend=backend,
                embedding_model=settings.SEARCH_LOCAL_MODEL if backend == "local" else None,
                status=VectorIndexStatus.PROCESSING,
            )
            db.add(vector_index)
            db.commit()
        else:
            vector_index.status = VectorIndexStatus.PROCESSING
            db.commit()

        # 获取视频信息
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            logger.error(f"Video not found: {video_id}")
            if vector_index:
                vector_index.status = VectorIndexStatus.FAILED
                vector_index.error_message = "Video not found"
                db.commit()
            return

        # 内嵌模式下，允许在 PROCESSING 阶段执行
        # 但仍需检查基本可用性
        subtitle_available = bool(subtitle_path and os.path.exists(subtitle_path))
        if video.status not in (VideoStatus.PROCESSING, VideoStatus.COMPLETED) and not subtitle_available:
            logger.warning(
                f"Video in unexpected state for inline indexing: " f"video_id={video_id}, status={video.status}"
            )
            vector_index.status = VectorIndexStatus.FAILED
            vector_index.error_message = f"Unexpected video status: {video.status}"
            video.has_semantic_index = False
            video.vector_index_id = None
            db.commit()
            return

        # 优先使用处理后文件，若没有则回退到原始上传文件。
        # 若视频文件缺失但字幕已存在，则降级为字幕索引。
        video_source_path = video.processed_filepath or video.filepath
        has_video_file = bool(video_source_path and os.path.exists(video_source_path))
        if not has_video_file and subtitle_available:
            logger.warning("Inline indexing fallback to subtitle-only mode | video_id=%s", video_id)
            video_source_path = f"video://{user_id}/{video_id}"
            has_video_file = True

        if not has_video_file:
            logger.error(f"No available video source for inline indexing: {video_id}")
            vector_index.status = VectorIndexStatus.FAILED
            vector_index.error_message = _missing_assets_error(
                video_id,
                video_path=video_source_path,
                subtitle_path=subtitle_path,
                status=str(video.status),
            )
            video.has_semantic_index = False
            video.vector_index_id = None
            db.commit()
            return

        # 验证字幕文件可用性
        if not subtitle_available:
            logger.warning(
                f"Subtitle file not available for inline indexing: "
                f"video_id={video_id}, subtitle_path={subtitle_path}"
            )
            vector_index.status = VectorIndexStatus.FAILED
            vector_index.error_message = "Subtitle file not available"
            video.has_semantic_index = False
            video.vector_index_id = None
            db.commit()
            return

        # 索引处理逻辑
        from app.services.search.search import build_video_index_internal

        chunk_count = build_video_index_internal(
            video_id=video_id,
            user_id=user_id,
            video_path=video_source_path,
            collection_name=vector_index.collection_name,
            backend=backend,
            db=db,
            subtitle_path=subtitle_path,
        )

        # 更新记录
        vector_index.chunk_count = chunk_count
        vector_index.status = VectorIndexStatus.COMPLETED
        vector_index.indexed_at = datetime.utcnow()
        vector_index.error_message = None

        video.has_semantic_index = True
        video.vector_index_id = vector_index.id

        db.commit()
        logger.info(f"Successfully completed inline indexing for video {video_id}: {chunk_count} chunks")

    except Exception as e:
        logger.error(f"Inline semantic indexing failed for video {video_id}: {e}", exc_info=True)
        has_updates = False
        if vector_index:
            vector_index.status = VectorIndexStatus.FAILED
            vector_index.error_message = str(e)[:500]  # 限制错误信息长度
            has_updates = True
        # 清理 Video 表中的索引标记，保持一致性
        if video:
            video.has_semantic_index = False
            video.vector_index_id = None
            has_updates = True
        if has_updates:
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to persist inline indexing failure state | video=%s user=%s",
                    video_id,
                    user_id,
                )
    finally:
        db.close()
