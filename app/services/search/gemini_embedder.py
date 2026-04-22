"""Gemini API 向量嵌入 - 改编自 SentrySearch"""

import base64
import logging
import time
from typing import List
from typing import Optional

logger = logging.getLogger(__name__)


class GeminiEmbedder:
    """使用 Google Gemini API 生成向量"""

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 Gemini 嵌入器

        Args:
            api_key: Google API Key（无则从环境变量获取）
        """
        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError("google-generativeai not installed. " "Install with: pip install google-generativeai")

        self.genai = genai

        if api_key:
            self.genai.configure(api_key=api_key)
        # else: 使用默认的 GOOGLE_API_KEY 环境变量

        self.model_name = "models/embedding-001"

    def embed_video_chunk(self, chunk_path: str, verbose: bool = False) -> List[float]:
        """将视频片段嵌入为向量"""
        import base64

        # 读取视频文件为 base64
        try:
            with open(chunk_path, "rb") as f:
                video_data = base64.standard_b64encode(f.read()).decode("utf-8")
        except FileNotFoundError:
            logger.error(f"Video chunk not found: {chunk_path}")
            raise

        if verbose:
            logger.info(f"Embedding video chunk: {chunk_path}")

        # 调用 Gemini API 生成向量
        # 注意：Gemini 的 embed_content 可以接收视频数据
        try:
            result = self.genai.embed_content(
                model=self.model_name,
                content={
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "video/mp4",
                                "data": video_data,
                            }
                        }
                    ]
                },
            )
            embedding = result["embedding"]
            if verbose:
                logger.info(f"Successfully embedded chunk, dimension: {len(embedding)}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to embed chunk: {e}")
            raise

    def embed_query(self, query_text: str, verbose: bool = False) -> List[float]:
        """将文本查询嵌入为向量"""
        if verbose:
            logger.info(f"Embedding query: {query_text[:50]}...")

        try:
            result = self.genai.embed_content(
                model=self.model_name,
                content=query_text,
            )
            embedding = result["embedding"]
            if verbose:
                logger.info(f"Successfully embedded query, dimension: {len(embedding)}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            raise

    def embed_batch(self, texts: List[str], verbose: bool = False) -> List[List[float]]:
        """批量嵌入文本"""
        embeddings = []
        for text in texts:
            try:
                embedding = self.embed_query(text, verbose=verbose)
                embeddings.append(embedding)
                # Gemini API 有速率限制，添加延迟
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to embed text: {e}")
                # 返回零向量作为备用
                embeddings.append([0.0] * 768)
        return embeddings
