"""语义处理工具 - FastAPI 版本

用于字幕的语义分段、合并和标题生成
"""

import json
import logging
import re
import time
from typing import List

import jieba.analyse
import requests
from app.core.config import settings
from app.utils.ollama_compat import build_ollama_options
from app.utils.ollama_compat import sanitize_ollama_response_text

logger = logging.getLogger(__name__)


def check_ollama_service() -> bool:
    """检查 Ollama 服务是否可用"""
    try:
        response = requests.get(f"{settings.OLLAMA_BASE_URL}/tags", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def generate_title_traditional(text: str, max_words: int = 4) -> str:
    """使用传统 NLP 方法生成标题"""
    try:
        if len(text) < 10:
            return text

        keywords = jieba.analyse.textrank(text, topK=max_words)
        if not keywords:
            return text[:10] + "..." if len(text) > 10 else text

        title = "".join(keywords)
        return title[:10] if len(title) > 10 else title
    except Exception as e:
        logger.error(f"生成标题时出错: {str(e)}")
        return text[:10] + "..." if len(text) > 10 else text


def generate_title_with_ollama(text: str) -> str:
    """使用 Ollama 生成标题"""
    if not text or not text.strip():
        return "无标题"

    try:
        if len(text) > 1000:
            text = text[:1000] + "..."

        prompt = f"""请为以下文本生成一个简短的标题（不超过10个字），标题应准确反映文本的主要内容：

{text}

要求：
1. 标题应简洁明确，不超过10个字
2. 直接返回标题，不要有任何其他内容"""

        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": build_ollama_options(temperature=0.3),
            },
            timeout=30,
        )

        if response.status_code == 200:
            title = sanitize_ollama_response_text(response.json().get("response", "")).strip()
            return title[:20] if len(title) > 20 else title
        return generate_title_traditional(text)
    except Exception as e:
        logger.error(f"Ollama 生成标题出错: {str(e)}")
        return generate_title_traditional(text)


def format_subtitle_text(text: str) -> str:
    """格式化字幕文本，添加适当的换行和标点"""
    try:
        # 处理句号、问号、感叹号后添加换行
        text = re.sub(r"([。！？.!?])\s*", r"\1\n\n", text)
        # 处理逗号和分号
        text = re.sub(r"([，,、;；:：])\s*", r"\1 ", text)
        # 处理连续换行符
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 合并连续空格
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()
    except Exception:
        return text


def merge_subtitles_by_semantics_ollama(subtitles: List[dict]) -> List[dict]:
    """使用 Ollama 进行语义分段和标题生成

    Args:
        subtitles: 字幕列表，每项包含 start_time, end_time, text

    Returns:
        合并后的字幕段落列表
    """
    if not subtitles:
        return []

    # 字幕太少，不进行合并
    if len(subtitles) < 5:
        for sub in subtitles:
            sub["title"] = sub["text"][:10] + "..." if len(sub["text"]) > 10 else sub["text"]
        return subtitles

    # 检查 Ollama 服务
    if not check_ollama_service():
        logger.warning("Ollama 服务不可用，使用简单分段方法")
        return _simple_merge_subtitles(subtitles)

    try:
        # 构建字幕文本
        full_text = ""
        for i, sub in enumerate(subtitles):
            full_text += f"[{i}] {sub['text']}\n"

        # 根据字幕数量确定分段策略
        subtitle_count = len(subtitles)
        if subtitle_count < 20:
            segment_guidance = "2-3个段落"
        elif subtitle_count < 50:
            segment_guidance = "4-6个段落"
        elif subtitle_count < 100:
            segment_guidance = "10-12个段落"
        elif subtitle_count < 150:
            segment_guidance = "15-17个段落"
        else:
            segment_guidance = "20-25个段落"

        prompt = f"""请分析以下视频字幕文本，并完成：
1. 根据语义连贯性分成若干段落
2. 为每个段落生成简短标题（不超过10个字）

字幕文本：
{full_text}

要求：
1. 根据语义和主题变化分段
2. 每个段落表达完整的意思
3. 共{subtitle_count}条字幕，应生成{segment_guidance}
4. 返回JSON格式

返回格式：
```json
[
  {{"start_index": 0, "end_index": 5, "title": "主题介绍"}},
  {{"start_index": 6, "end_index": 12, "title": "要点讨论"}}
]
```"""

        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": build_ollama_options(temperature=0.3),
            },
            timeout=120,
        )

        if response.status_code != 200:
            logger.error(f"Ollama API 调用失败: {response.status_code}")
            return _simple_merge_subtitles(subtitles)

        # 解析响应
        response_text = sanitize_ollama_response_text(response.json().get("response", ""))

        # 提取 JSON
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response_text)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response_text

        # 清理 JSON 字符串
        json_str = re.sub(r"^[^[{]*", "", json_str)
        json_str = re.sub(r"[^}\]]*$", "", json_str)

        try:
            segments_info = json.loads(json_str)
        except json.JSONDecodeError:
            logger.error(f"无法解析 JSON 响应: {json_str[:100]}")
            return _simple_merge_subtitles(subtitles)

        if not isinstance(segments_info, list):
            return _simple_merge_subtitles(subtitles)

        # 构建合并后的字幕
        merged_subtitles = []
        for segment in segments_info:
            start_index = segment.get("start_index", 0)
            end_index = segment.get("end_index", 0)
            title = segment.get("title", "")

            # 确保索引有效
            start_index = max(0, min(start_index, len(subtitles) - 1))
            end_index = max(start_index, min(end_index, len(subtitles) - 1))

            # 合并文本
            original_text = " ".join([subtitles[i]["text"] for i in range(start_index, end_index + 1)])
            formatted_text = format_subtitle_text(original_text)

            if not title:
                title = generate_title_traditional(formatted_text)

            merged_subtitles.append(
                {
                    "start_time": subtitles[start_index]["start_time"],
                    "end_time": subtitles[end_index]["end_time"],
                    "text": formatted_text,
                    "title": title[:10] if len(title) > 10 else title,
                    "original_indices": list(range(start_index, end_index + 1)),
                }
            )

        if not merged_subtitles:
            return _simple_merge_subtitles(subtitles)

        # 按时间排序
        merged_subtitles.sort(key=lambda x: x["start_time"])
        logger.info(f"语义分段完成，共 {len(merged_subtitles)} 个段落")
        return merged_subtitles

    except Exception as e:
        logger.error(f"语义分段出错: {str(e)}")
        return _simple_merge_subtitles(subtitles)


def _simple_merge_subtitles(subtitles: List[dict]) -> List[dict]:
    """简单的字幕合并方法（备用）"""
    if not subtitles:
        return []

    # 根据字幕数量确定每段包含的字幕数
    total = len(subtitles)
    if total < 20:
        per_segment = max(3, total // 3)
    elif total < 50:
        per_segment = max(5, total // 5)
    elif total < 100:
        per_segment = max(8, total // 10)
    else:
        per_segment = max(10, total // 15)

    results = []
    for i in range(0, total, per_segment):
        end_idx = min(i + per_segment - 1, total - 1)

        # 合并文本
        segment_text = " ".join([subtitles[j]["text"] for j in range(i, end_idx + 1)])
        formatted_text = format_subtitle_text(segment_text)

        # 生成标题
        title = generate_title_traditional(segment_text)

        results.append(
            {
                "start_time": subtitles[i]["start_time"],
                "end_time": subtitles[end_idx]["end_time"],
                "text": formatted_text,
                "title": title,
                "original_indices": list(range(i, end_idx + 1)),
            }
        )

    logger.info(f"简单分段完成，共 {len(results)} 个段落")
    return results
