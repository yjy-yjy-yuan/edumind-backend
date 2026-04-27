"""视频删除后的异步清理任务。"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.note import Note, NoteTimestamp
from app.models.qa import Question
from app.models.subtitle import Subtitle
from app.models.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def _safe_remove_file(path: Optional[str], *, video_id: int, label: str) -> None:
    target = str(path or "").strip()
    if not target:
        return
    if not os.path.exists(target):
        return
    try:
        os.remove(target)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "异步清理删除文件失败（忽略）| video_id=%s | label=%s | path=%s | error=%s", video_id, label, target, exc
        )


def _purge_video_semantic_index(db, *, video_id: int, user_id: int) -> dict:
    vector_index_rows = db.query(VectorIndex).filter(VectorIndex.video_id == video_id).all()
    collection_names = {
        str(row.collection_name).strip() for row in vector_index_rows if getattr(row, "collection_name", None)
    }
    collection_names.add(f"user_{user_id}_video_{video_id}_chunks")

    removed_collections = []
    failed_collections = []

    try:
        import chromadb
        from chromadb.config import Settings as ChromaClientSettings

        telemetry_enabled = bool(settings.SEARCH_CHROMA_ANONYMIZED_TELEMETRY)
        os.environ["ANONYMIZED_TELEMETRY"] = "TRUE" if telemetry_enabled else "FALSE"
        telemetry_impl = (
            "chromadb.telemetry.product.posthog.Posthog"
            if telemetry_enabled
            else "app.services.search.chroma_telemetry.NoOpTelemetryClient"
        )
        client_settings = ChromaClientSettings(
            anonymized_telemetry=telemetry_enabled,
            chroma_product_telemetry_impl=telemetry_impl,
            chroma_telemetry_impl=telemetry_impl,
        )
        client = chromadb.PersistentClient(path=settings.SEARCH_CHROMA_DB_DIR, settings=client_settings)

        for collection_name in sorted(name for name in collection_names if name):
            try:
                client.delete_collection(name=collection_name)
                removed_collections.append(collection_name)
            except Exception as exc:  # noqa: BLE001
                failed_collections.append({"collection": collection_name, "error": str(exc)[:180]})
    except Exception as exc:  # noqa: BLE001
        logger.warning("异步清理初始化向量库客户端失败 | video_id=%s | error=%s", video_id, exc)
        failed_collections.append({"collection": "<client_init>", "error": str(exc)[:180]})

    vector_indexes_deleted = (
        db.query(VectorIndex).filter(VectorIndex.video_id == video_id).delete(synchronize_session=False)
    )
    vector_indices_deleted = 0
    try:
        result = db.execute(
            text("DELETE FROM vector_indices WHERE video_id = :video_id"),
            {"video_id": video_id},
        )
        vector_indices_deleted = int(getattr(result, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("异步清理删除 vector_indices 失败 | video_id=%s | error=%s", video_id, exc)

    return {
        "vector_indexes_deleted": int(vector_indexes_deleted or 0),
        "vector_indices_deleted": vector_indices_deleted,
        "collections_deleted": removed_collections,
        "collections_delete_failed": failed_collections,
    }


def cleanup_deleted_video_resources(
    video_id: int,
    user_id: int,
    file_path: str = "",
    processed_path: str = "",
    preview_path: str = "",
    subtitle_path: str = "",
) -> dict:
    """异步清理视频关联数据与索引。"""
    db = SessionLocal()
    try:
        db.query(Question).filter(Question.video_id == video_id).delete(synchronize_session=False)
        db.query(Subtitle).filter(Subtitle.video_id == video_id).delete(synchronize_session=False)

        note_ids = [row[0] for row in db.query(Note.id).filter(Note.video_id == video_id).all()]
        if note_ids:
            db.query(NoteTimestamp).filter(NoteTimestamp.note_id.in_(note_ids)).delete(synchronize_session=False)
        db.query(Note).filter(Note.video_id == video_id).delete(synchronize_session=False)

        index_cleanup = _purge_video_semantic_index(db, video_id=video_id, user_id=user_id)

        _safe_remove_file(file_path, video_id=video_id, label="filepath")
        _safe_remove_file(processed_path, video_id=video_id, label="processed_filepath")
        _safe_remove_file(preview_path, video_id=video_id, label="preview_filepath")
        _safe_remove_file(subtitle_path, video_id=video_id, label="subtitle_filepath")

        db.commit()
        logger.info("异步清理完成 | video_id=%s | user_id=%s", video_id, user_id)
        return {"ok": True, "index_cleanup": index_cleanup}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("异步清理失败 | video_id=%s | user_id=%s | error=%s", video_id, user_id, exc)
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()
