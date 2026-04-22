"""搜索与推荐共享的相似度融合函数。"""

from __future__ import annotations

import re
from typing import Optional

_TEXT_CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize_text(text: Optional[str]) -> str:
    """归一化文本，提升关键词重排稳定性。"""
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    return _TEXT_CLEAN_RE.sub("", raw)


def char_ngrams(text: str, n: int) -> set[str]:
    """生成字符 n-gram 集合。"""
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def lexical_overlap_score(query: str, candidate: Optional[str]) -> float:
    """计算查询词与候选文本的词面重合度（0-1）。"""
    q = normalize_text(query)
    c = normalize_text(candidate)
    if not q or not c:
        return 0.0

    if q in c:
        return 1.0

    q_chars = set(q)
    c_chars = set(c)
    char_overlap = len(q_chars & c_chars) / max(1, len(q_chars))

    q_bigrams = char_ngrams(q, 2)
    c_bigrams = char_ngrams(c, 2)
    bigram_overlap = len(q_bigrams & c_bigrams) / max(1, len(q_bigrams))

    return min(1.0, 0.4 * char_overlap + 0.6 * bigram_overlap)


def fused_similarity_score(
    *,
    vector_similarity: float,
    query: str,
    preview_text: Optional[str],
    video_title: Optional[str],
) -> float:
    """
    融合向量分数与词面匹配分数，缓解“相关性普遍虚高且排序反直觉”问题。
    """
    semantic = max(0.0, min(1.0, float(vector_similarity)))
    lexical_preview = lexical_overlap_score(query, preview_text)
    lexical_title = lexical_overlap_score(query, video_title)
    lexical = max(lexical_preview, 0.85 * lexical_title)

    # 语义为主、词面为辅：让“关键词明显命中”的结果更靠前，同时压低无关高分噪声。
    fused = semantic * 0.75 + lexical * 0.35
    return max(0.0, min(1.0, fused))
