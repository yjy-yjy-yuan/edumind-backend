"""搜索服务的结构化日志记录模块（经 app.analytics 统一管道输出）"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class SearchEventLogger:
    """搜索事件日志记录器 — 向后兼容；底层写入集中式 AnalyticsTelemetry。"""

    @staticmethod
    def _log_event(event: dict) -> None:
        """记录 JSON 事件（统一 schema + 可配置 logger 级别）。"""
        try:
            from app.analytics.adapters.search import emit_search_legacy_event
            from app.analytics.pipeline import get_telemetry

            emit_search_legacy_event(get_telemetry(), event)
        except Exception as exc:
            logger.debug("search analytics emit skipped: %s", exc, exc_info=True)

    @staticmethod
    def log_search_request(
        user_id: int,
        query_text: str,
        video_ids: Optional[list] = None,
        threshold: float = 0.5,
        limit: int = 20,
        trace_id: Optional[str] = None,
    ) -> None:
        """记录搜索请求入口（建议透传上游 X-Trace-Id）。"""
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "search_request_received",
            "user_id": user_id,
            "query_length": len(query_text),
            "video_count": len(video_ids) if video_ids else 0,
            "threshold": threshold,
            "limit": limit,
            "search_scope": "single_video" if video_ids else "all_videos",
        }
        if trace_id:
            payload["trace_id"] = trace_id[:128]
        SearchEventLogger._log_event(payload)

    @staticmethod
    def log_adaptive_chunking_selected(
        video_id: int, video_duration_seconds: float, chunk_duration: int, overlap: int
    ) -> None:
        """记录自适应切片参数选择"""
        SearchEventLogger._log_event(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event": "adaptive_chunking_selected",
                "video_id": video_id,
                "video_duration_seconds": video_duration_seconds,
                "chunk_duration": chunk_duration,
                "overlap": overlap,
            }
        )

    @staticmethod
    def log_video_chunking_completed(video_id: int, chunk_count: int, duration_ms: float) -> None:
        """记录视频分片完成"""
        SearchEventLogger._log_event(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event": "video_chunking_completed",
                "video_id": video_id,
                "chunk_count": chunk_count,
                "duration_ms": round(duration_ms, 1),
            }
        )

    @staticmethod
    def log_embedding_batch_completed(video_id: int, batch_size: int, duration_ms: float) -> None:
        """记录嵌入批次完成"""
        SearchEventLogger._log_event(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event": "embedding_batch_completed",
                "video_id": video_id,
                "batch_size": batch_size,
                "duration_ms": round(duration_ms, 1),
            }
        )

    @staticmethod
    def log_indexing_completed(video_id: int, chunk_count: int, backend: str, duration_ms: float, user_id: int) -> None:
        """记录索引完成"""
        SearchEventLogger._log_event(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event": "indexing_completed",
                "video_id": video_id,
                "chunk_count": chunk_count,
                "backend": backend,
                "duration_ms": round(duration_ms, 1),
                "user_id": user_id,
            }
        )

    @staticmethod
    def log_indexing_failed(video_id: int, error_stage: str, error_message: str, user_id: int) -> None:
        """记录索引失败"""
        SearchEventLogger._log_event(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event": "indexing_failed",
                "video_id": video_id,
                "error_stage": error_stage,
                "error_message": str(error_message)[:200],  # 截断长错误
                "user_id": user_id,
            }
        )

    @staticmethod
    def log_search_completed(
        user_id: int,
        query_text: str,
        video_count: int,
        result_count: int,
        duration_ms: float,
        max_similarity: Optional[float] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """记录搜索完成"""
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "search_completed",
            "user_id": user_id,
            "query_length": len(query_text),
            "video_count": video_count,
            "result_count": result_count,
            "duration_ms": round(duration_ms, 1),
            "max_similarity": round(max_similarity, 3) if max_similarity else None,
        }
        if trace_id:
            payload["trace_id"] = trace_id[:128]
        SearchEventLogger._log_event(payload)

    @staticmethod
    def log_search_failed(user_id: int, error_message: str, trace_id: Optional[str] = None) -> None:
        """记录搜索失败"""
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "search_failed",
            "user_id": user_id,
            "error_message": str(error_message)[:200],
        }
        if trace_id:
            payload["trace_id"] = trace_id[:128]
        SearchEventLogger._log_event(payload)

    @staticmethod
    def log_chromadb_search_executed(
        videos_searched: int, results_found: int, duration_ms: float, trace_id: Optional[str] = None
    ) -> None:
        """记录 ChromaDB 搜索执行"""
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "chromadb_search_executed",
            "videos_searched": videos_searched,
            "results_found": results_found,
            "duration_ms": round(duration_ms, 1),
        }
        if trace_id:
            payload["trace_id"] = trace_id[:128]
        SearchEventLogger._log_event(payload)
