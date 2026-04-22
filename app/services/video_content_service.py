"""视频内容分析服务。"""

import json
import logging
import re
from collections import Counter
from typing import Iterable
from typing import Optional

import jieba
import jieba.analyse
import requests
from app.core.config import settings
from app.utils.ollama_compat import build_ollama_options
from app.utils.ollama_compat import sanitize_ollama_response_text

logger = logging.getLogger(__name__)

SUMMARY_STYLE_BRIEF = "brief"
SUMMARY_STYLE_STUDY = "study"
SUMMARY_STYLE_DETAILED = "detailed"
SUPPORTED_SUMMARY_STYLES = {
    SUMMARY_STYLE_BRIEF,
    SUMMARY_STYLE_STUDY,
    SUMMARY_STYLE_DETAILED,
}
DEFAULT_ONLINE_MODEL = "qwen-plus"
DEFAULT_MAX_TAGS = 6
DEFAULT_PRIMARY_TOPIC_NAME_LENGTH = 40

STOP_WORDS = {
    "",
    "的",
    "了",
    "和",
    "是",
    "在",
    "就",
    "都",
    "而",
    "及",
    "与",
    "着",
    "或",
    "一个",
    "一种",
    "我们",
    "你们",
    "他们",
    "以及",
    "因为",
    "所以",
    "这个",
    "那个",
    "如果",
    "然后",
    "进行",
    "可以",
    "需要",
    "通过",
    "这里",
    "就是",
    "这些",
    "那些",
    "已经",
    "还是",
    "并且",
    "主要",
    "关于",
    "一个",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "are",
    "was",
    "were",
    "been",
    "have",
    "has",
    "had",
    "you",
    "your",
    "our",
    "their",
}

GENERIC_VIDEO_TITLE_PREFIXES = ("local-", "bilibili-", "youtube-", "mooc-")
GENERIC_VIDEO_TITLE_VALUES = {
    "",
    "video",
    "lesson",
    "upload",
    "local",
    "本地视频",
    "未命名视频",
    "视频",
}
SUBJECT_KEYWORDS = {
    "数学": (
        "数学",
        "小学数学",
        "初中数学",
        "高中数学",
        "高数",
        "高等数学",
        "数学分析",
        "数分",
        "微积分",
        "导数",
        "极限",
        "函数",
        "几何",
        "几何证明",
        "代数",
        "线代",
        "线性代数",
        "概率",
        "统计",
        "计数",
        "数列",
        "三角",
        "三角形",
        "解析几何",
        "勾股",
        "勾股定理",
        "排列",
        "组合",
        "排列组合",
        "组合数学",
        "插空法",
        "空位计数",
    ),
    "英语": (
        "英语",
        "英文",
        "英语写作",
        "英文写作",
        "英语听力",
        "英语阅读",
        "英语口语",
        "英语语法",
        "单词",
        "词汇",
        "雅思",
        "托福",
        "四六级",
    ),
    "语文": ("语文", "作文", "文言文", "古诗", "古诗词", "现代文", "阅读理解", "修辞", "汉语"),
    "物理": ("物理", "力学", "电学", "电磁", "电路", "热学", "光学", "受力", "牛顿", "动量", "加速度"),
    "化学": ("化学", "有机", "无机", "化学方程式", "化学反应", "酸碱", "氧化还原", "元素周期"),
    "生物": ("生物", "细胞", "遗传", "基因", "生态", "光合作用", "呼吸作用", "进化"),
    "历史": ("历史", "中国史", "世界史", "近代史", "古代史", "现代史"),
    "地理": ("地理", "地图", "气候", "地貌", "经纬度", "区域地理"),
    "政治": ("政治", "思想政治", "时政", "哲学", "法治", "马克思主义"),
    "计算机": (
        "计算机",
        "编程",
        "程序设计",
        "python",
        "java",
        "c++",
        "前端",
        "后端",
        "算法",
        "数据结构",
        "数据库",
        "机器学习",
        "人工智能",
        "深度学习",
    ),
    "经济": ("经济", "金融", "会计", "财务", "投资", "证券", "宏观经济", "微观经济"),
}
TAG_ALIAS_MAP = {
    "高数": "高等数学",
    "数分": "数学分析",
    "线代": "线性代数",
    "概率论": "概率统计",
    "英文": "英语",
    "python语言": "Python",
    "py": "Python",
}


def normalize_summary_style(style: str) -> str:
    normalized = str(style or "").strip().lower()
    if normalized in SUPPORTED_SUMMARY_STYLES:
        return normalized
    return SUMMARY_STYLE_STUDY


def clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_multiline_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def ensure_sentence_tail(text: str) -> str:
    sentence = clean_whitespace(text).rstrip("；;，,、")
    if not sentence:
        return ""
    if sentence[-1] in "。！？!?":
        return sentence
    return f"{sentence}。"


def remove_srt_timing_markers(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n")
    normalized = re.sub(r"^\d+\s*$", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(
        r"^\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*$",
        "",
        normalized,
        flags=re.MULTILINE,
    )
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    return clean_whitespace(normalized)


def read_subtitle_text(subtitle_path: str) -> str:
    if not subtitle_path:
        return ""
    try:
        with open(subtitle_path, "r", encoding="utf-8") as handle:
            return remove_srt_timing_markers(handle.read())
    except FileNotFoundError:
        return ""
    except Exception as exc:
        logger.warning("读取字幕文本失败 | path=%s | error=%s", subtitle_path, exc)
        return ""


def extract_transcript_text(result: Optional[dict]) -> str:
    payload = result or {}
    segments = payload.get("segments") or []
    texts = [clean_whitespace(segment.get("text")) for segment in segments if clean_whitespace(segment.get("text"))]
    if texts:
        return clean_whitespace(" ".join(texts))
    return clean_whitespace(payload.get("text") or "")


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"[\r\n]+", "\n", str(text or ""))
    chunks = re.split(r"(?<=[。！？!?；;])\s+|\n+", normalized)
    sentences = []
    for chunk in chunks:
        sentence = clean_whitespace(chunk)
        if len(sentence) < 6:
            continue
        sentences.append(sentence)
    return sentences


def tokenize_sentence(text: str) -> list[str]:
    if re.search(r"[\u4e00-\u9fff]", text):
        tokens = [token.strip().lower() for token in jieba.lcut(text, cut_all=False)]
    else:
        tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", text)]
    return [token for token in tokens if len(token) >= 2 and token not in STOP_WORDS]


def sentence_limit_for_style(style: str) -> int:
    if style == SUMMARY_STYLE_BRIEF:
        return 3
    if style == SUMMARY_STYLE_DETAILED:
        return 7
    return 5


def render_summary(sentences: list[str], title: str, style: str) -> str:
    cleaned = [ensure_sentence_tail(sentence) for sentence in sentences if ensure_sentence_tail(sentence)]
    if not cleaned:
        return ""

    if style == SUMMARY_STYLE_BRIEF:
        prefix = f"《{title}》" if title else "本视频"
        compact = "；".join(sentence.rstrip("。") for sentence in cleaned[:3])
        return f"{prefix}主要内容：{compact}。"

    lines = []
    if title:
        lines.append(f"主题：{title}")
    intro = "学习重点：" if style == SUMMARY_STYLE_STUDY else "详细梳理："
    lines.append(intro)
    for index, sentence in enumerate(cleaned, start=1):
        lines.append(f"{index}. {sentence}")
    return "\n".join(lines)


def fallback_summary(text: str, *, title: str = "", style: str = SUMMARY_STYLE_STUDY) -> str:
    sentences = split_sentences(text)
    if not sentences:
        trimmed = clean_whitespace(text)[:220]
        return render_summary([trimmed], title, style) if trimmed else ""

    if len(sentences) <= sentence_limit_for_style(style):
        return render_summary(sentences, title, style)

    token_scores = Counter()
    sentence_tokens: list[list[str]] = []
    for sentence in sentences:
        tokens = tokenize_sentence(sentence)
        sentence_tokens.append(tokens)
        token_scores.update(tokens)

    scored = []
    for index, sentence in enumerate(sentences):
        tokens = sentence_tokens[index]
        if not tokens:
            score = 0.0
        else:
            score = sum(token_scores[token] for token in tokens) / len(tokens)
            if index == 0:
                score += 0.15
        scored.append((score, index, sentence))

    limit = sentence_limit_for_style(style)
    selected_indexes = sorted(index for _, index, _ in sorted(scored, reverse=True)[:limit])
    selected_sentences = [sentences[index] for index in selected_indexes]
    return render_summary(selected_sentences, title, style)


def parse_json_array(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []

    candidates = [raw]
    matched = re.search(r"\[[\s\S]*\]", raw)
    if matched:
        candidates.insert(0, matched.group(0))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return [clean_whitespace(item) for item in payload if clean_whitespace(item)]
    return []


def normalize_learning_tag(tag: str) -> str:
    text = clean_whitespace(tag).strip("#")
    if not text:
        return ""
    return TAG_ALIAS_MAP.get(text.lower(), text).strip()


def normalize_tags(tags: Iterable[str], *, max_tags: int = DEFAULT_MAX_TAGS) -> list[str]:
    normalized = []
    seen = set()
    for tag in tags:
        text = normalize_learning_tag(tag)
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
        if len(normalized) >= max(1, int(max_tags)):
            break
    return normalized


def infer_subject_from_text(*, tags: Iterable[str], title: str = "", summary: str = "") -> str:
    scores: Counter[str] = Counter()
    normalized_tags = [normalize_learning_tag(tag).lower() for tag in tags if normalize_learning_tag(tag)]
    normalized_title = str(title or "").lower()
    normalized_summary = str(summary or "").lower()

    for subject, keywords in SUBJECT_KEYWORDS.items():
        for keyword in keywords:
            normalized_keyword = keyword.lower()
            if any(
                tag == normalized_keyword or normalized_keyword in tag or tag in normalized_keyword
                for tag in normalized_tags
            ):
                scores[subject] += 5
            if normalized_keyword and normalized_keyword in normalized_title:
                scores[subject] += 3
            if normalized_keyword and normalized_keyword in normalized_summary:
                scores[subject] += 1

    if not scores:
        return ""
    return scores.most_common(1)[0][0]


def build_subject_enriched_tags(
    tags: Iterable[str], *, title: str = "", summary: str = "", max_tags: int = DEFAULT_MAX_TAGS
) -> list[str]:
    normalized_tags = normalize_tags(tags, max_tags=max(1, int(max_tags)))
    subject = infer_subject_from_text(tags=normalized_tags, title=title, summary=summary)
    ordered = [subject, *normalized_tags] if subject else normalized_tags
    return normalize_tags(ordered, max_tags=max_tags)


def fallback_tags(summary: str, *, title: str = "", max_tags: int = DEFAULT_MAX_TAGS) -> list[str]:
    seed = clean_whitespace(f"{title} {summary}")
    tags = []
    try:
        tags = jieba.analyse.extract_tags(seed, topK=max(1, int(max_tags)))
    except Exception as exc:
        logger.warning("离线标签提取失败，回退到词频统计 | error=%s", exc)

    if not tags:
        counter = Counter(tokenize_sentence(seed))
        tags = [token for token, _ in counter.most_common(max(1, int(max_tags)))]
    return normalize_tags(tags, max_tags=max_tags)


def strip_generated_video_title(title: str) -> str:
    text = clean_whitespace(title)
    lowered = text.lower()
    for prefix in GENERIC_VIDEO_TITLE_PREFIXES:
        if lowered.startswith(prefix):
            return clean_whitespace(text[len(prefix) :])
    return text


def looks_like_generic_video_title(title: str) -> bool:
    text = strip_generated_video_title(title)
    lowered = text.lower()
    if lowered in GENERIC_VIDEO_TITLE_VALUES:
        return True
    return bool(re.fullmatch(r"(video|lesson|upload|本地视频|视频)[\s_-]*\d*", lowered))


def normalize_primary_topic_name(name: str, *, max_length: int = DEFAULT_PRIMARY_TOPIC_NAME_LENGTH) -> str:
    text = clean_multiline_text(name)
    if not text:
        return ""

    lines = [clean_whitespace(line) for line in text.splitlines() if clean_whitespace(line)]
    candidate = lines[0] if lines else text
    candidate = re.sub(r"^(?:文件名|文件标题|标题|命名)[:：]\s*", "", candidate)
    candidate = re.sub(r"^(?:主题|课程主题|视频主题)[:：]\s*", "", candidate)
    candidate = re.sub(r"^(?:学习重点|详细梳理|主要内容)[:：]?\s*", "", candidate)
    candidate = re.sub(r"^(?:[-*•]\s*|\d+[\.、:：]\s*)", "", candidate)
    candidate = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", candidate)
    candidate = re.sub(r"^[《“\"'`]+|[》”\"'`]+$", "", candidate)
    candidate = re.split(r"[。！？!?；;]", candidate, maxsplit=1)[0]
    candidate = re.sub(r"\s+", " ", candidate)
    candidate = re.sub(r'[<>:"/\\|?*]+', " ", candidate)
    candidate = candidate.strip(" ._-")
    if not candidate:
        return ""
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip(" ._-")
    return candidate


def extract_primary_topic_candidates(summary: str, *, title: str = "") -> list[str]:
    candidates = []
    normalized_title = strip_generated_video_title(title)
    if normalized_title and not looks_like_generic_video_title(normalized_title):
        candidates.append(normalized_title)

    lines = [clean_whitespace(line) for line in clean_multiline_text(summary).splitlines() if clean_whitespace(line)]
    for line in lines:
        normalized = normalize_primary_topic_name(line)
        if not normalized:
            continue
        if looks_like_generic_video_title(normalized):
            continue
        candidates.append(normalized)

    return candidates


def build_tag_based_primary_topic(tags: Iterable[str], *, max_length: int = DEFAULT_PRIMARY_TOPIC_NAME_LENGTH) -> str:
    normalized_tags = [
        normalize_primary_topic_name(tag, max_length=max_length) for tag in normalize_tags(tags, max_tags=3)
    ]
    normalized_tags = [tag for tag in normalized_tags if tag]
    if not normalized_tags:
        return ""

    if len(normalized_tags) >= 2:
        combined = f"{normalized_tags[0]}-{normalized_tags[1]}"
        if len(combined) <= max_length:
            return combined
    return normalized_tags[0][:max_length].rstrip(" ._-")


def fallback_primary_topic_name(
    summary: str,
    *,
    tags: Iterable[str] = (),
    title: str = "",
    max_length: int = DEFAULT_PRIMARY_TOPIC_NAME_LENGTH,
) -> str:
    candidates = extract_primary_topic_candidates(summary, title=title)
    normalized_tags = normalize_tags(tags, max_tags=3)

    for candidate in candidates:
        if len(candidate) < 2:
            continue
        if normalized_tags and any(tag in candidate for tag in normalized_tags[:2]):
            return normalize_primary_topic_name(candidate, max_length=max_length)

    for candidate in candidates:
        normalized = normalize_primary_topic_name(candidate, max_length=max_length)
        if len(normalized) >= 2:
            return normalized

    tag_candidate = build_tag_based_primary_topic(normalized_tags, max_length=max_length)
    if tag_candidate:
        return tag_candidate

    normalized_title = normalize_primary_topic_name(strip_generated_video_title(title), max_length=max_length)
    if normalized_title and not looks_like_generic_video_title(normalized_title):
        return normalized_title
    return ""


def ollama_available() -> bool:
    try:
        response = requests.get(f"{settings.OLLAMA_BASE_URL}/tags", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def call_online_chat(prompt: str, *, system_prompt: str, model: str = DEFAULT_ONLINE_MODEL) -> Optional[str]:
    api_key = clean_whitespace(settings.OPENAI_API_KEY or settings.QWEN_API_KEY)
    base_url = clean_whitespace(settings.OPENAI_BASE_URL or settings.QWEN_API_BASE).rstrip("/")
    if not api_key or not base_url:
        return None

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        return clean_multiline_text(payload["choices"][0]["message"]["content"])
    except Exception as exc:
        logger.warning("在线摘要/标签生成失败，准备回退 | error=%s", exc)
        return None


def call_ollama(prompt: str, *, system_prompt: str, model: Optional[str] = None) -> Optional[str]:
    if not ollama_available():
        return None

    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/generate",
            json={
                "model": model or settings.OLLAMA_MODEL,
                "prompt": f"{system_prompt}\n\n{prompt}",
                "stream": False,
                "options": build_ollama_options(
                    temperature=0.3,
                    num_predict=1024,
                ),
            },
            timeout=120,
        )
        response.raise_for_status()
        return clean_multiline_text(sanitize_ollama_response_text(response.json().get("response", "")))
    except Exception as exc:
        logger.warning("Ollama 摘要/标签生成失败，准备回退 | error=%s", exc)
        return None


def build_summary_prompt(transcript_text: str, *, title: str, style: str) -> tuple[str, str]:
    style_instruction = {
        SUMMARY_STYLE_BRIEF: "输出 1 段精炼摘要，突出主题和结论，控制在 90 字以内。",
        SUMMARY_STYLE_STUDY: "输出适合学习复盘的摘要，先写一句总述，再列 3 到 5 条学习重点。",
        SUMMARY_STYLE_DETAILED: "输出较详细的学习摘要，先总述，再列 5 到 7 条关键知识点或推导脉络。",
    }[style]
    system_prompt = "你是一个课程视频学习助手，负责基于字幕生成准确、可复习的中文摘要。不要编造字幕中没有出现的事实。"
    prompt = (
        f"视频标题：{title or '未命名视频'}\n"
        f"摘要风格：{style}\n"
        f"{style_instruction}\n"
        "请直接输出摘要正文，不要解释你的思路。\n\n"
        f"字幕内容：\n{transcript_text[:9000]}"
    )
    return system_prompt, prompt


def build_tag_prompt(summary: str, *, title: str, max_tags: int) -> tuple[str, str]:
    system_prompt = "你是一个课程视频标签助手。请基于视频摘要提炼简短中文标签，只返回 JSON 数组。"
    prompt = (
        f"视频标题：{title or '未命名视频'}\n"
        f"请提取 {max(1, int(max_tags))} 个标签。\n"
        "要求：标签应简短、可检索、避免重复，不要输出解释文字。\n"
        f"摘要内容：\n{summary}\n\n"
        '输出格式示例：["导数定义", "几何意义", "求导法则"]'
    )
    return system_prompt, prompt


def build_primary_topic_name_prompt(summary: str, *, tags: Iterable[str], title: str) -> tuple[str, str]:
    tag_text = "、".join(normalize_tags(tags, max_tags=6)) or "无"
    system_prompt = "你是一个课程视频命名助手。请根据摘要和标签提炼最核心的学习主题，只输出一个简短中文标题。"
    prompt = (
        f"原始标题：{strip_generated_video_title(title) or '未命名视频'}\n"
        f"摘要：\n{clean_multiline_text(summary)[:1800]}\n\n"
        f"标签：{tag_text}\n\n"
        "请输出一个适合作为本地视频文件名和数据库标题的中文名称。\n"
        "要求：\n"
        "1. 只输出标题本身，不要解释。\n"
        "2. 突出最主要的知识主题，不要空泛词。\n"
        "3. 控制在 8 到 24 个字之间，不能包含文件扩展名。\n"
        "4. 不要使用引号、括号、序号、斜杠。"
    )
    return system_prompt, prompt


def generate_video_summary(
    video_id: int,
    subtitle_path: str = "",
    *,
    transcript_text: str = "",
    title: str = "",
    style: str = SUMMARY_STYLE_STUDY,
) -> dict:
    normalized_style = normalize_summary_style(style)
    source_text = clean_whitespace(transcript_text) or read_subtitle_text(subtitle_path)
    if not source_text:
        logger.debug(
            "视频摘要生成跳过 | video_id=%s | style=%s | subtitle_path=%s | transcript_len=%s",
            video_id,
            normalized_style,
            subtitle_path,
            len(clean_whitespace(transcript_text)),
        )
        return {"success": False, "error": "无法提取字幕文本"}

    logger.debug(
        "视频摘要生成开始 | video_id=%s | style=%s | title=%s | source_len=%s | subtitle_path=%s",
        video_id,
        normalized_style,
        title,
        len(source_text),
        subtitle_path,
    )
    system_prompt, prompt = build_summary_prompt(source_text, title=title, style=normalized_style)
    logger.debug(
        "视频摘要 prompt prepared | video_id=%s | system_prompt_len=%s | prompt_len=%s",
        video_id,
        len(system_prompt),
        len(prompt),
    )
    summary = call_online_chat(prompt, system_prompt=system_prompt) or call_ollama(prompt, system_prompt=system_prompt)
    provider = "ai"
    logger.debug(
        "视频摘要模型返回 | video_id=%s | provider=%s | summary_len=%s",
        video_id,
        provider,
        len(summary or ""),
    )
    if not summary:
        summary = fallback_summary(source_text, title=title, style=normalized_style)
        provider = "fallback"
        logger.debug(
            "视频摘要回退到本地规则 | video_id=%s | style=%s | summary_len=%s",
            video_id,
            normalized_style,
            len(summary or ""),
        )

    summary = clean_multiline_text(summary)
    if not summary:
        logger.debug("视频摘要结果为空 | video_id=%s | provider=%s", video_id, provider)
        return {"success": False, "error": "摘要内容为空"}

    logger.info("视频摘要生成完成 | video_id=%s | provider=%s | style=%s", video_id, provider, normalized_style)
    return {"success": True, "summary": summary, "provider": provider, "style": normalized_style}


def generate_video_tags(video_id: int, summary: str, *, title: str = "", max_tags: int = DEFAULT_MAX_TAGS) -> dict:
    clean_summary = clean_whitespace(summary)
    if not clean_summary:
        logger.debug("视频标签生成跳过 | video_id=%s | title=%s | reason=empty_summary", video_id, title)
        return {"success": False, "error": "摘要内容为空"}

    logger.debug(
        "视频标签生成开始 | video_id=%s | title=%s | summary_len=%s | max_tags=%s",
        video_id,
        title,
        len(clean_summary),
        max_tags,
    )
    system_prompt, prompt = build_tag_prompt(clean_summary, title=title, max_tags=max_tags)
    logger.debug(
        "视频标签 prompt prepared | video_id=%s | system_prompt_len=%s | prompt_len=%s",
        video_id,
        len(system_prompt),
        len(prompt),
    )
    raw_tags = call_online_chat(prompt, system_prompt=system_prompt) or call_ollama(prompt, system_prompt=system_prompt)
    provider = "ai"
    logger.debug(
        "视频标签模型返回 | video_id=%s | provider=%s | raw_tags_len=%s", video_id, provider, len(raw_tags or "")
    )

    tags = normalize_tags(parse_json_array(raw_tags), max_tags=max_tags) if raw_tags else []
    if not tags:
        tags = fallback_tags(clean_summary, title=title, max_tags=max_tags)
        provider = "fallback"
        logger.debug("视频标签回退到本地规则 | video_id=%s | fallback_count=%s", video_id, len(tags))
    tags = build_subject_enriched_tags(tags, title=title, summary=clean_summary, max_tags=max_tags)

    if not tags:
        logger.debug("视频标签结果为空 | video_id=%s", video_id)
        return {"success": False, "error": "标签生成失败"}

    logger.info("视频标签生成完成 | video_id=%s | provider=%s | count=%s", video_id, provider, len(tags))
    return {"success": True, "tags": tags, "provider": provider}


def generate_primary_topic_name(
    summary: str,
    *,
    tags: Iterable[str] = (),
    title: str = "",
    max_length: int = DEFAULT_PRIMARY_TOPIC_NAME_LENGTH,
) -> dict:
    clean_summary = clean_multiline_text(summary)
    normalized_tags = normalize_tags(tags, max_tags=6)
    if not clean_summary and not normalized_tags:
        logger.debug("主标题生成跳过 | reason=empty_inputs | title=%s", title)
        return {"success": False, "error": "摘要和标签均为空"}

    logger.debug(
        "主标题生成开始 | title=%s | summary_len=%s | tags=%s | max_length=%s",
        title,
        len(clean_summary),
        normalized_tags[:6],
        max_length,
    )
    system_prompt, prompt = build_primary_topic_name_prompt(clean_summary, tags=normalized_tags, title=title)
    logger.debug(
        "主标题 prompt prepared | system_prompt_len=%s | prompt_len=%s",
        len(system_prompt),
        len(prompt),
    )
    raw_name = call_online_chat(prompt, system_prompt=system_prompt)
    provider = "ai"
    logger.debug("主标题模型返回 | provider=%s | raw_name_len=%s", provider, len(raw_name or ""))

    primary_name = normalize_primary_topic_name(raw_name, max_length=max_length) if raw_name else ""
    if not primary_name:
        primary_name = fallback_primary_topic_name(
            clean_summary,
            tags=normalized_tags,
            title=title,
            max_length=max_length,
        )
        provider = "fallback"
        logger.debug("主标题回退到本地规则 | provider=%s | title=%s", provider, primary_name)

    if not primary_name:
        logger.debug("主标题结果为空 | title=%s", title)
        return {"success": False, "error": "无法生成主标题"}

    logger.info("视频主标题生成完成 | provider=%s | title=%s", provider, primary_name)
    return {"success": True, "name": primary_name, "provider": provider}
