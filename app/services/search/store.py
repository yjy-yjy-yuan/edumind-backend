"""ChromaDB 向量存储包装 - 改编自 SentrySearch"""

import hashlib
import logging
import os
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional

import chromadb
from app.core.config import settings
from chromadb.config import Settings as ChromaClientSettings

logger = logging.getLogger(__name__)


def make_chunk_id(source_file: str, start_time: float, end_time: float, preview_text: str = "") -> str:
    """生成确定的分片 ID"""
    raw = f"{source_file}:{start_time}:{end_time}:{preview_text[:80]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class EduMindStore:
    """
    ChromaDB 向量存储包装，用于 EduMind 语义搜索

    特性：
    - 多用户隔离（独立集合命名）
    - 支持不同的嵌入后端（Gemini、本地模型）
    - 批量操作优化
    """

    def __init__(
        self,
        db_path: str,
        collection_name: str,
        backend: str = "gemini",
        model: Optional[str] = None,
    ):
        """
        初始化向量存储

        Args:
            db_path: ChromaDB 数据目录
            collection_name: 集合名（应遵循 user_{user_id}_video_{video_id}_chunks）
            backend: 嵌入后端（gemini 或 local）
            model: 本地模型名称（如 qwen8b）
        """
        db_path = str(Path(db_path).resolve())
        Path(db_path).mkdir(parents=True, exist_ok=True)

        # 显式关闭匿名遥测，避免运行时 posthog 噪声日志干扰排障。
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
        self._client = chromadb.PersistentClient(path=db_path, settings=client_settings)
        self._backend = backend
        self._model = model
        self._collection_name = collection_name

        metadata = {
            "hnsw:space": "cosine",
            "embedding_backend": backend,
        }
        if model:
            metadata["embedding_model"] = model
        self._collection_metadata = metadata

        self._collection = self._get_or_recover_collection()
        logger.info(f"Initialized ChromaDB collection: {collection_name}")

    @staticmethod
    def _is_chroma_collection_corrupted(exc: BaseException) -> bool:
        """
        检测 Chroma SQLite 元数据损坏/版本不兼容导致的典型异常。

        常见报错:
        - TypeError: object of type 'int' has no len()
        - _decode_seq_id / max_seqid 相关调用栈
        """
        text = str(exc).lower()
        cause = str(exc.__cause__).lower() if exc.__cause__ else ""
        stack = f"{text} {cause}"
        if "object of type 'int' has no len()" in stack:
            return True
        return "_decode_seq_id" in stack or "max_seqid" in stack

    def _recreate_collection(self) -> chromadb.Collection:
        """删除并重建当前 collection（用于损坏自愈）。"""
        try:
            self._client.delete_collection(name=self._collection_name)
            logger.warning("Deleted corrupted Chroma collection: %s", self._collection_name)
        except Exception as delete_exc:  # noqa: BLE001
            logger.warning("Failed to delete collection during recovery (%s): %s", self._collection_name, delete_exc)

        return self._client.get_or_create_collection(name=self._collection_name, metadata=self._collection_metadata)

    def _get_or_recover_collection(self) -> chromadb.Collection:
        """初始化 collection；若检测到损坏则尝试一次恢复。"""
        try:
            return self._client.get_or_create_collection(name=self._collection_name, metadata=self._collection_metadata)
        except Exception as exc:  # noqa: BLE001
            if self._is_chroma_collection_corrupted(exc):
                logger.error("Detected corrupted Chroma collection '%s', recreating...", self._collection_name)
                return self._recreate_collection()
            raise

    @property
    def collection(self) -> chromadb.Collection:
        """获取 ChromaDB 集合"""
        return self._collection

    def get_backend(self) -> str:
        """获取嵌入后端"""
        meta = self._collection.metadata or {}
        return meta.get("embedding_backend", "gemini")

    def get_model(self) -> Optional[str]:
        """获取本地模型名称"""
        meta = self._collection.metadata or {}
        return meta.get("embedding_model")

    def add_chunk(
        self,
        chunk_id: str,
        embedding: List[float],
        metadata: Dict,
    ) -> None:
        """
        存储单个分片的向量和元数据

        Args:
            chunk_id: 分片 ID
            embedding: 向量嵌入
            metadata: 元数据（需要包含 source_file, start_time, end_time）
        """
        meta = {
            "source_file": str(metadata["source_file"]),
            "start_time": float(metadata["start_time"]),
            "end_time": float(metadata["end_time"]),
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }

        # 保留额外的元数据
        for key in metadata:
            if key not in meta and key not in ("embedding", "chunk_id"):
                meta[key] = metadata[key]

        self._collection.upsert(
            ids=[chunk_id],
            embeddings=[embedding],
            metadatas=[meta],
        )

    def add_chunks_batch(self, chunks: List[dict]) -> None:
        """
        批量存储分片

        Args:
            chunks: 分片列表，每个包含 source_file, start_time, end_time, embedding
        """
        now = datetime.now(timezone.utc).isoformat()
        ids = []
        embeddings = []
        metadatas = []

        for chunk in chunks:
            chunk_id = make_chunk_id(
                chunk["source_file"],
                chunk["start_time"],
                chunk["end_time"],
                str(chunk.get("preview_text") or chunk.get("text") or ""),
            )
            ids.append(chunk_id)
            embeddings.append(chunk["embedding"])
            meta = {
                "source_file": str(chunk["source_file"]),
                "start_time": float(chunk["start_time"]),
                "end_time": float(chunk["end_time"]),
                "indexed_at": now,
            }
            for key, value in chunk.items():
                if key not in {"chunk_id", "embedding", "source_file", "start_time", "end_time"}:
                    meta[key] = value
            metadatas.append(meta)

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info(f"Added {len(chunks)} chunks to collection {self._collection_name}")

    def search(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        threshold: float = 0.5,
    ) -> List[dict]:
        """
        搜索相似分片

        Args:
            query_embedding: 查询向量
            n_results: 返回结果数
            threshold: 相似度阈值（0-1）

        Returns:
            结果列表，包含 source_file, start_time, end_time, similarity_score
        """
        try:
            count = self._collection.count()
            if count == 0:
                return []

            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, count),
            )
        except Exception as exc:  # noqa: BLE001
            if self._is_chroma_collection_corrupted(exc):
                logger.error(
                    "Chroma collection corrupted during search: %s. Requires re-index for this video.",
                    self._collection_name,
                )
            raise

        hits = []
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            similarity = max(0.0, 1.0 - (float(distance) / 2.0))  # cosine distance [0,2] -> similarity [0,1]

            # 应用阈值过滤
            if similarity >= threshold:
                hits.append(
                    {
                        "chunk_id": results["ids"][0][i],
                        "source_file": meta["source_file"],
                        "start_time": meta["start_time"],
                        "end_time": meta["end_time"],
                        "preview_text": meta.get("preview_text"),
                        "similarity_score": similarity,
                        "distance": distance,
                    }
                )

        return hits

    def is_indexed(self, source_file: str) -> bool:
        """检查文件是否已索引"""
        try:
            results = self._collection.get(
                where={"source_file": source_file},
                limit=1,
            )
            return len(results["ids"]) > 0
        except Exception as exc:  # noqa: BLE001
            if self._is_chroma_collection_corrupted(exc):
                logger.error(
                    "Chroma collection corrupted in is_indexed (%s). Recreating collection for recovery.",
                    self._collection_name,
                )
                self._collection = self._recreate_collection()
                return False
            raise

    def remove_file(self, source_file: str) -> int:
        """删除文件的所有分片"""
        try:
            results = self._collection.get(where={"source_file": source_file})
            ids = results["ids"]
            if ids:
                self._collection.delete(ids=ids)
                logger.info(f"Removed {len(ids)} chunks for {source_file}")
            return len(ids)
        except Exception as exc:  # noqa: BLE001
            if self._is_chroma_collection_corrupted(exc):
                logger.error(
                    "Chroma collection corrupted in remove_file (%s). Recreating collection.",
                    self._collection_name,
                )
                self._collection = self._recreate_collection()
                return 0
            raise

    def get_chunk_count(self) -> int:
        """获取总分片数"""
        try:
            return self._collection.count()
        except Exception as exc:  # noqa: BLE001
            if self._is_chroma_collection_corrupted(exc):
                logger.error(
                    "Chroma collection corrupted in get_chunk_count (%s). Recreating collection.",
                    self._collection_name,
                )
                self._collection = self._recreate_collection()
                return 0
            raise

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._collection.count()
        if total == 0:
            return {
                "total_chunks": 0,
                "unique_source_files": 0,
                "source_files": [],
            }

        all_meta = self._collection.get(include=["metadatas"])
        source_files = sorted({m["source_file"] for m in all_meta["metadatas"]})
        return {
            "total_chunks": total,
            "unique_source_files": len(source_files),
            "source_files": source_files,
        }
