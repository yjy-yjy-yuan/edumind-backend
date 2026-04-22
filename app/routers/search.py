"""语义搜索 API 路由"""

import logging
from typing import List

from app.core.config import settings
from app.core.database import get_db
from app.models.vector_index import VectorIndex
from app.models.vector_index import VectorIndexStatus
from app.models.video import Video
from app.schemas.search import IndexingStatusResponse
from app.schemas.search import SearchResultChunk
from app.schemas.search import SemanticSearchRequest
from app.schemas.search import SemanticSearchResponse
from app.services.search.search import SemanticSearchBackendUnavailableError
from app.services.search.search import semantic_search_videos
from app.services.search.search_log import is_global_semantic_search_request
from app.services.search.search_log import maybe_record_global_semantic_search
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])


def get_current_user_id(request: Request, db: Session = Depends(get_db)) -> int:
    """
    获取当前用户 ID

    优先级:
    1. 请求头中的 X-User-ID
    2. 会话中的 user_id（需要集成认证系统）
    3. 默认用户 1（开发环境）
    """
    # 尝试从请求头获取
    user_id = request.headers.get("X-User-ID")
    if user_id:
        try:
            return int(user_id)
        except (ValueError, TypeError):
            pass

    # TODO: 集成实际认证系统（如 JWT、会话等）
    # 当前返回默认用户 1（开发/测试用）
    logger.warning("Using default user_id=1 (no authentication header found)")
    return 1


def verify_user_video_access(user_id: int, video_id: int, db: Session) -> Video:
    """验证用户对视频的访问权限"""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user_id).first()
    if not video:
        raise HTTPException(status_code=403, detail="无权访问此视频")
    return video


@router.post("/semantic/search")
async def semantic_search(
    http_request: Request,
    request: SemanticSearchRequest,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
) -> SemanticSearchResponse:
    """
    语义搜索 API

    请求体：
    - query: 自然语言查询
    - video_ids: 可选，限定搜索范围
    - limit: 返回结果数（1-100）
    - threshold: 相似度阈值（0-1）
    """
    if not settings.SEARCH_ENABLED:
        raise HTTPException(status_code=503, detail="语义搜索功能未启用")

    # 验证查询字符串
    if not request.query or len(request.query.strip()) == 0:
        raise HTTPException(status_code=400, detail="查询内容不能为空")
    if len(request.query) > 500:
        raise HTTPException(status_code=400, detail="查询内容过长（最多 500 字符）")

    trace_header = http_request.headers.get("X-Trace-Id") or http_request.headers.get("X-Request-Id")
    trace_id = trace_header.strip()[:128] if trace_header and trace_header.strip() else None

    is_global_request = is_global_semantic_search_request(request.video_ids)
    query_stripped = request.query.strip()

    # 确定搜索范围内的视频
    video_ids = request.video_ids or []
    if not video_ids:
        # 获取用户所有已索引的视频
        videos = db.query(Video).filter(Video.user_id == current_user_id, Video.has_semantic_index.is_(True)).all()
        video_ids = [v.id for v in videos]
    else:
        # 验证用户对所有指定视频的访问权限，并仅保留已构建索引的视频
        indexed_video_ids = []
        for vid in video_ids:
            video = verify_user_video_access(current_user_id, vid, db)
            if video.has_semantic_index:
                indexed_video_ids.append(video.id)

        video_ids = indexed_video_ids

    if not video_ids:
        maybe_record_global_semantic_search(
            db,
            is_global_request=is_global_request,
            user_id=current_user_id,
            query_text=query_stripped,
            video_ids_searched=[],
            result_count=0,
            total_time_ms=0,
            limit_used=request.limit,
            threshold_used=request.threshold,
        )
        return SemanticSearchResponse(
            query=request.query,
            results=[],
            total_time_ms=0,
            message="没有可搜索的视频（需要先构建索引）",
        )

    try:
        import time

        start_time = time.time()

        results = semantic_search_videos(
            query=request.query,
            video_ids=video_ids,
            user_id=current_user_id,
            limit=request.limit,
            threshold=request.threshold,
            db=db,
            trace_id=trace_id,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        maybe_record_global_semantic_search(
            db,
            is_global_request=is_global_request,
            user_id=current_user_id,
            query_text=query_stripped,
            video_ids_searched=video_ids,
            result_count=len(results),
            total_time_ms=elapsed_ms,
            limit_used=request.limit,
            threshold_used=request.threshold,
        )

        return SemanticSearchResponse(
            query=request.query,
            results=results,
            total_time_ms=elapsed_ms,
        )
    except SemanticSearchBackendUnavailableError as e:
        logger.error("Semantic search backend unavailable for user %s: %s", current_user_id, e, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="语义索引暂不可用，请在视频库重建索引后重试（可先触发“重建索引”）。",
        )
    except Exception as e:
        logger.error(f"Search failed for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="搜索服务暂时不可用")


@router.post("/videos/{video_id}/index")
async def trigger_video_indexing(
    video_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
) -> IndexingStatusResponse:
    """手动触发视频索引"""
    if not settings.SEARCH_ENABLED:
        raise HTTPException(status_code=503, detail="语义搜索功能未启用")

    video = verify_user_video_access(current_user_id, video_id, db)

    # 获取或创建索引记录
    vector_index = (
        db.query(VectorIndex).filter(VectorIndex.video_id == video_id, VectorIndex.user_id == current_user_id).first()
    )

    if vector_index is None:
        collection_name = f"user_{current_user_id}_video_{video_id}_chunks"
        vector_index = VectorIndex(
            video_id=video_id,
            user_id=current_user_id,
            collection_name=collection_name,
            embedding_backend=settings.SEARCH_BACKEND,
            embedding_model=settings.SEARCH_LOCAL_MODEL if settings.SEARCH_BACKEND == "local" else None,
            status=VectorIndexStatus.PENDING,
        )
        db.add(vector_index)
    else:
        vector_index.status = VectorIndexStatus.PENDING

    db.commit()

    # 提交后台任务
    from app.core.executor import submit_task
    from app.tasks.vector_indexing import index_video_for_search

    logger.info(f"Submitting indexing task for video {video_id}")
    submit_task(index_video_for_search, video_id, current_user_id, settings.SEARCH_BACKEND)

    return IndexingStatusResponse(
        video_id=video_id, status=VectorIndexStatus.PENDING.value, progress=0, chunk_count=0, message="索引任务已提交"
    )


@router.get("/videos/{video_id}/index/status")
async def get_indexing_status(
    video_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
) -> IndexingStatusResponse:
    """获取视频的索引构建状态"""
    verify_user_video_access(current_user_id, video_id, db)

    vector_index = (
        db.query(VectorIndex).filter(VectorIndex.video_id == video_id, VectorIndex.user_id == current_user_id).first()
    )

    if vector_index is None:
        return IndexingStatusResponse(
            video_id=video_id, status="not_indexed", progress=0, chunk_count=0, message="未构建索引"
        )

    progress = (
        100
        if vector_index.status == VectorIndexStatus.COMPLETED
        else (50 if vector_index.status == VectorIndexStatus.PROCESSING else 0)
    )

    return IndexingStatusResponse(
        video_id=video_id,
        status=vector_index.status.value,
        progress=progress,
        chunk_count=vector_index.chunk_count,
        indexed_at=vector_index.indexed_at,
        error_message=vector_index.error_message,
        message=f"状态: {vector_index.status.value}",
    )
