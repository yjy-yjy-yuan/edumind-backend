"""本地模型向量嵌入"""

import logging
from typing import List
from typing import Optional

from sklearn.feature_extraction.text import HashingVectorizer

logger = logging.getLogger(__name__)


class LocalEmbedder:
    """使用本地哈希向量器生成文本嵌入，适合字幕语义搜索。"""

    def __init__(
        self,
        model_name: str = "hashing-char-ngrams-zh",
        dimensions: int = 768,
        quantize: Optional[str] = None,
    ):
        """
        初始化本地嵌入器

        Args:
            model_name: 逻辑模型名（用于日志和元数据）
            dimensions: 向量维度
            quantize: 量化方式（当前未使用，保留兼容）
        """
        self.model_name = model_name
        self.dimensions = dimensions
        self.quantize = quantize
        self._vectorizer = HashingVectorizer(
            n_features=dimensions,
            alternate_sign=False,
            analyzer="char",
            ngram_range=(2, 4),
            norm="l2",
            lowercase=False,
        )
        logger.info("LocalEmbedder initialized | model=%s | dimensions=%s", model_name, dimensions)

    def _embed_text(self, text: str) -> List[float]:
        normalized = str(text or "").strip()
        if not normalized:
            return [0.0] * self.dimensions
        matrix = self._vectorizer.transform([normalized])
        return matrix.toarray()[0].astype(float).tolist()

    def embed_video_chunk(self, chunk_path: str, verbose: bool = False) -> List[float]:
        """
        本地后端不直接处理视频像素嵌入。

        当前打通方案优先使用字幕文本构建索引；若走到这里，说明缺少字幕支撑。
        """
        raise RuntimeError(
            "Local semantic search currently requires subtitle text chunks; video-only embedding is unsupported."
        )

    def embed_query(self, query_text: str, verbose: bool = False) -> List[float]:
        """将文本查询嵌入为向量。"""
        if verbose:
            logger.info("Embedding local query: %s", query_text[:50])
        return self._embed_text(query_text)

    def embed_batch(self, texts: List[str], verbose: bool = False) -> List[List[float]]:
        """批量嵌入文本。"""
        normalized = [str(text or "").strip() for text in texts]
        if not normalized:
            return []
        matrix = self._vectorizer.transform(normalized)
        return matrix.toarray().astype(float).tolist()
