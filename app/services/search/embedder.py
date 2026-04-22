"""嵌入工厂 - 改编自 SentrySearch"""

import logging
from typing import List
from typing import Optional

logger = logging.getLogger(__name__)

_current_embedder = None


class BaseEmbedder:
    """向量嵌入基类"""

    def embed_video_chunk(self, chunk_path: str, verbose: bool = False) -> List[float]:
        """将视频片段嵌入为向量"""
        raise NotImplementedError

    def embed_query(self, query_text: str, verbose: bool = False) -> List[float]:
        """将文本查询嵌入为向量"""
        raise NotImplementedError


def get_embedder(backend: str = "gemini", **kwargs) -> BaseEmbedder:
    """获取或创建嵌入器实例"""
    global _current_embedder

    if backend == "gemini":
        from app.core.config import settings
        from app.services.search.gemini_embedder import GeminiEmbedder

        if _current_embedder is None or not isinstance(_current_embedder, GeminiEmbedder):
            api_key = kwargs.get("api_key") or settings.SEARCH_GEMINI_API_KEY
            _current_embedder = GeminiEmbedder(api_key=api_key)
        return _current_embedder
    elif backend == "local":
        from app.services.search.local_embedder import LocalEmbedder

        model = kwargs.get("model", "qwen8b")
        dims = kwargs.get("dimensions", 768)
        quantize = kwargs.get("quantize", None)
        if _current_embedder is None or not isinstance(_current_embedder, LocalEmbedder):
            _current_embedder = LocalEmbedder(model_name=model, dimensions=dims, quantize=quantize)
        return _current_embedder
    else:
        raise ValueError(f"Unknown backend: {backend}")


def reset_embedder():
    """重置嵌入器（用于切换后端）"""
    global _current_embedder
    _current_embedder = None


def embed_video_chunk(chunk_path: str, verbose: bool = False) -> List[float]:
    """便利函数：嵌入视频片段"""
    embedder = get_embedder()
    return embedder.embed_video_chunk(chunk_path, verbose=verbose)


def embed_query(query_text: str, verbose: bool = False) -> List[float]:
    """便利函数：嵌入查询"""
    embedder = get_embedder()
    return embedder.embed_query(query_text, verbose=verbose)
