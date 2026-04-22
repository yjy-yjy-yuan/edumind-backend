"""视频问答 RAG 工具。"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Generator
from typing import Optional

import requests
from app.core.config import settings
from app.services.video_content_service import clean_multiline_text
from app.services.video_content_service import clean_whitespace
from app.services.video_content_service import tokenize_sentence

logger = logging.getLogger(__name__)

SUPPORTED_QA_PROVIDERS = {"qwen", "deepseek"}
SRT_TIME_RE = re.compile(r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})")


class QAConfigError(RuntimeError):
    """问答模型配置异常。"""


class QAProviderError(RuntimeError):
    """问答模型调用异常。"""


@dataclass
class KnowledgeChunk:
    """可检索的知识片段。"""

    text: str
    source_type: str
    source_label: str
    order: int
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    boost: float = 0.0

    @property
    def retrieval_text(self) -> str:
        return clean_whitespace(f"{self.source_label} {self.text}")

    @property
    def time_range(self) -> str:
        if self.start_time is None or self.end_time is None:
            return ""
        return f"{format_seconds(self.start_time)}-{format_seconds(self.end_time)}"

    def to_reference(self, index: int, time_order: Optional[int] = None) -> dict:
        preview = clean_whitespace(self.text)
        if len(preview) > 120:
            preview = f"{preview[:117]}..."
        return {
            "index": index,
            "source_type": self.source_type,
            "label": self.source_label,
            "time_range": self.time_range,
            "time_order": time_order,
            "preview": preview,
        }


@dataclass
class ConversationMessage:
    """标准化后的对话历史消息。"""

    role: str
    content: str


def format_seconds(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timecode(text: str) -> float:
    hours, minutes, remain = str(text or "00:00:00,000").replace(".", ",").split(":")
    seconds, milliseconds = remain.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def normalize_provider(provider: str = "", model: str = "") -> str:
    raw_provider = clean_whitespace(provider).lower()
    if raw_provider in SUPPORTED_QA_PROVIDERS:
        return raw_provider

    raw_model = clean_whitespace(model).lower()
    if raw_model.startswith("deepseek"):
        return "deepseek"
    if raw_model.startswith("qwen"):
        return "qwen"

    fallback = clean_whitespace(settings.QA_DEFAULT_PROVIDER).lower()
    return fallback if fallback in SUPPORTED_QA_PROVIDERS else "qwen"


def resolve_provider_label(provider: str) -> str:
    return "DeepSeek" if provider == "deepseek" else "通义千问"


def build_stream_event(event_type: str, **payload) -> dict:
    event = {"type": event_type}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str) and not value and key not in {"answer", "model"}:
            continue
        event[key] = value
    return event


def resolve_provider_credentials(provider: str) -> tuple[str, str]:
    normalized_provider = normalize_provider(provider)
    if normalized_provider == "deepseek":
        api_key = clean_whitespace(settings.DEEPSEEK_API_KEY)
        base_url = clean_whitespace(settings.DEEPSEEK_API_BASE).rstrip("/")
        env_hint = "DEEPSEEK_API_KEY / DEEPSEEK_API_BASE"
    else:
        api_key = clean_whitespace(settings.QWEN_API_KEY or settings.OPENAI_API_KEY)
        base_url = clean_whitespace(settings.QWEN_API_BASE or settings.OPENAI_BASE_URL).rstrip("/")
        env_hint = "QWEN_API_KEY / OPENAI_API_KEY 与 QWEN_API_BASE / OPENAI_BASE_URL"

    if not api_key or not base_url:
        raise QAConfigError(f"{resolve_provider_label(normalized_provider)} 问答未配置，请检查 {env_hint}")

    return api_key, base_url


def resolve_model(provider: str, model: str = "", deep_thinking: bool = False) -> str:
    normalized_provider = normalize_provider(provider, model)
    requested_model = clean_whitespace(model)
    if requested_model:
        return requested_model
    if normalized_provider == "deepseek" and deep_thinking:
        return clean_whitespace(settings.DEEPSEEK_REASONER_MODEL) or "deepseek-reasoner"
    if normalized_provider == "deepseek":
        return clean_whitespace(settings.DEEPSEEK_QA_MODEL) or "deepseek-chat"
    return clean_whitespace(settings.QWEN_QA_MODEL) or "qwen-plus"


def extract_message_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return clean_multiline_text(content)
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
        return clean_multiline_text("".join(text_parts))
    return clean_multiline_text(str(content or ""))


def call_deepseek_reasoner_stream(
    messages: list[dict],
    *,
    model: str = "",
) -> Generator[tuple[str, str], None, None]:
    """流式调用 DeepSeek Reasoner，yield (thinking_chunk, answer_chunk)。

    DeepSeek SSE 格式:
    data: {"choices":[{"delta":{"role":"assistant","content":"","thinking_content":"..."}}]}
    data: {"choices":[{"delta":{"thinking_content":"...","content":"..."}}]}
    data: [DONE]
    """
    api_key, base_url = resolve_provider_credentials("deepseek")
    resolved_model = (
        clean_whitespace(model) or clean_whitespace(settings.DEEPSEEK_REASONER_MODEL) or "deepseek-reasoner"
    )

    with requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": resolved_model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        },
        stream=True,
        timeout=180,
    ) as response:
        if response.status_code >= 400:
            detail = clean_whitespace(response.text)[:240] or f"HTTP {response.status_code}"
            raise QAProviderError(f"DeepSeek 流式调用失败：{detail}")

        for line in response.iter_lines():
            raw = line.strip()
            if raw == b"data: [DONE]":
                break
            if not raw or not raw.startswith(b"data: "):
                continue
            try:
                chunk = json.loads(raw[6:])
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                thinking = delta.get("thinking_content") or ""
                answer = delta.get("content") or ""
                if thinking or answer:
                    yield thinking, answer
            except (json.JSONDecodeError, IndexError, TypeError) as exc:
                logger.warning("解析 DeepSeek 流式响应失败: %s | line=%s", exc, raw[:80])
                continue


def qa_search_tokens(text: str) -> list[str]:
    normalized = clean_whitespace(text)
    if not normalized:
        return []

    tokens = tokenize_sentence(normalized)
    if tokens:
        return tokens

    english_tokens = re.findall(r"[A-Za-z0-9_]+", normalized.lower())
    if english_tokens:
        return english_tokens

    return re.findall(r"[\u4e00-\u9fff]", normalized)


def parse_tags(raw_tags: str) -> list[str]:
    if not raw_tags:
        return []
    try:
        payload = json.loads(raw_tags)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [clean_whitespace(str(item)) for item in payload if clean_whitespace(str(item))]


def normalize_history_messages(history: Optional[list]) -> list[ConversationMessage]:
    limit = max(0, int(settings.QA_MAX_HISTORY_MESSAGES))
    if not history or limit <= 0:
        return []

    normalized: list[ConversationMessage] = []
    total_chars = 0
    max_chars = max(200, int(settings.QA_MAX_HISTORY_CHARS))

    for item in history[-limit:]:
        if isinstance(item, dict):
            role = clean_whitespace(item.get("role")).lower()
            content = clean_multiline_text(item.get("content") or "")
        else:
            role = clean_whitespace(getattr(item, "role", "")).lower()
            content = clean_multiline_text(getattr(item, "content", "") or "")

        if role == "ai":
            role = "assistant"
        if role not in {"user", "assistant"} or not content:
            continue

        trimmed_content = content[:500]
        if total_chars and total_chars + len(trimmed_content) > max_chars:
            break

        normalized.append(ConversationMessage(role=role, content=trimmed_content))
        total_chars += len(trimmed_content)

    return normalized


def build_retrieval_query(question: str, history_messages: list[ConversationMessage]) -> str:
    recent_user_messages = [message.content for message in history_messages if message.role == "user"][-2:]
    query_parts = [*recent_user_messages, clean_whitespace(question)]
    return clean_whitespace("\n".join(part for part in query_parts if part))


def parse_srt_chunks(subtitle_path: str) -> list[dict]:
    if not subtitle_path:
        return []

    try:
        with open(subtitle_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("读取字幕文件失败 | path=%s | error=%s", subtitle_path, exc)
        return []

    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n"))
    chunks = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        time_line = lines[1] if lines[0].isdigit() else lines[0]
        match = SRT_TIME_RE.search(time_line)
        if not match:
            continue

        text_lines = lines[2:] if lines[0].isdigit() else lines[1:]
        text = clean_whitespace(" ".join(text_lines))
        if not text:
            continue

        chunks.append(
            {
                "start_time": parse_timecode(match.group("start")),
                "end_time": parse_timecode(match.group("end")),
                "text": text,
            }
        )
    return chunks


def build_subtitle_chunks(subtitles: list[dict], *, max_chars: int = 280, max_items: int = 6) -> list[KnowledgeChunk]:
    ordered = sorted(subtitles, key=lambda item: float(item.get("start_time") or 0.0))
    chunks: list[KnowledgeChunk] = []
    buffer: list[dict] = []
    current_chars = 0
    chunk_order = 1

    def flush_buffer() -> None:
        nonlocal buffer, current_chars, chunk_order
        if not buffer:
            return

        merged_text = clean_whitespace(" ".join(str(item.get("text") or "") for item in buffer))
        if merged_text:
            chunks.append(
                KnowledgeChunk(
                    text=merged_text,
                    source_type="subtitle",
                    source_label=f"字幕片段 {chunk_order}",
                    order=chunk_order,
                    start_time=float(buffer[0].get("start_time") or 0.0),
                    end_time=float(buffer[-1].get("end_time") or 0.0),
                    boost=0.08,
                )
            )
            chunk_order += 1

        buffer = []
        current_chars = 0

    for item in ordered:
        text = clean_whitespace(str(item.get("text") or ""))
        if not text:
            continue

        if buffer and (current_chars + len(text) > max_chars or len(buffer) >= max_items):
            flush_buffer()

        buffer.append(item)
        current_chars += len(text)

    flush_buffer()
    return chunks


def compute_bm25_score(
    question_tokens: list[str],
    document_tokens: list[str],
    document_frequency: Counter,
    total_docs: int,
    average_doc_length: float,
) -> float:
    if not question_tokens or not document_tokens:
        return 0.0

    token_counts = Counter(document_tokens)
    doc_length = max(1, len(document_tokens))
    k1 = 1.5
    b = 0.75
    score = 0.0

    for token in question_tokens:
        if token not in token_counts:
            continue

        df = document_frequency.get(token, 0)
        idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
        freq = token_counts[token]
        denominator = freq + k1 * (1 - b + b * doc_length / max(average_doc_length, 1.0))
        score += idf * freq * (k1 + 1) / denominator

    return score


def rank_chunks(question: str, chunks: list[KnowledgeChunk], *, top_k: int) -> list[KnowledgeChunk]:
    if not chunks:
        return []

    normalized_question = clean_whitespace(question)
    question_tokens = qa_search_tokens(normalized_question)
    if not question_tokens:
        return chunks[: max(1, top_k)]

    documents = [qa_search_tokens(chunk.retrieval_text) for chunk in chunks]
    document_frequency: Counter = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))

    average_doc_length = sum(max(1, len(tokens)) for tokens in documents) / max(1, len(documents))
    scored_chunks = []
    lowered_question = normalized_question.lower()

    for chunk, tokens in zip(chunks, documents):
        score = compute_bm25_score(question_tokens, tokens, document_frequency, len(documents), average_doc_length)
        lowered_text = chunk.text.lower()
        if lowered_question and lowered_question in lowered_text:
            score += 1.2
        token_hits = sum(1 for token in set(question_tokens) if token in lowered_text)
        score += min(0.6, token_hits * 0.08)
        score += chunk.boost
        scored_chunks.append((score, chunk.order, chunk))

    positive_hits = [item for item in scored_chunks if item[0] > 0]
    if positive_hits:
        ranked = sorted(positive_hits, key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[: max(1, top_k)]]

    summary_like = [chunk for chunk in chunks if chunk.source_type in {"summary", "tags"}]
    if summary_like:
        return summary_like[: max(1, top_k)]
    return chunks[: max(1, top_k)]


def build_context_text(chunks: list[KnowledgeChunk], *, max_chars: int) -> str:
    lines = []
    total_chars = 0
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"[{index}] {chunk.source_label}"
        if chunk.time_range:
            prefix = f"{prefix} ({chunk.time_range})"
        line = f"{prefix}\n{chunk.text}"
        if total_chars and total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line)
    return "\n\n".join(lines)


def build_video_qa_messages(
    question: str,
    *,
    video_title: str,
    context_text: str,
    history_messages: list[ConversationMessage],
) -> list[dict]:
    system_prompt = (
        "你是 EduMind 的视频学习问答助手。"
        "你只能基于检索到的视频字幕、摘要和标签回答，严禁编造上下文中不存在的事实。"
        "回答请使用中文，先给结论，再补充 2 到 4 条依据；若证据不足，要明确说明无法确认。"
        "如果用户连续追问，请结合已有对话历史理解代词、省略和上下文指代。"
    )
    messages = [{"role": "system", "content": system_prompt}]
    for item in history_messages:
        messages.append({"role": item.role, "content": item.content})

    user_prompt = (
        f"视频标题：{video_title or '未命名视频'}\n"
        f"用户问题：{clean_whitespace(question)}\n\n"
        "检索到的上下文如下：\n"
        f"{context_text or '无可用上下文'}\n\n"
        "输出要求：\n"
        "1. 优先直接回答问题。\n"
        "2. 如果用到了具体上下文，请在句末用 [1] [2] 这样的编号引用片段。\n"
        "3. 如果当前字幕不足以支撑结论，请明确写“根据当前字幕内容无法确认”。\n"
        "4. 不要输出思维过程。"
    )
    messages.append({"role": "user", "content": user_prompt})
    return messages


def build_free_qa_messages(question: str, *, history_messages: list[ConversationMessage]) -> list[dict]:
    system_prompt = (
        "你是 EduMind 的学习助手。请结合最近对话历史理解连续追问，用中文直接、简洁地回答用户问题，不要输出思维过程。"
    )
    messages = [{"role": "system", "content": system_prompt}]
    for item in history_messages:
        messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": f"用户问题：{clean_whitespace(question)}"})
    return messages


def resolve_answering_status(provider: str, *, deep_thinking: bool) -> tuple[str, str]:
    normalized_provider = normalize_provider(provider)
    if normalized_provider == "deepseek" and deep_thinking:
        return "reasoning", "正在调用 DeepSeek 思考模型"
    if normalized_provider == "deepseek":
        return "answering", "正在调用 DeepSeek 直接回答"
    return "answering", "正在调用通义千问回答"


def call_provider_chat(messages: list[dict], *, provider: str, model: str) -> str:
    normalized_provider = normalize_provider(provider, model)
    api_key, base_url = resolve_provider_credentials(normalized_provider)

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "stream": False,
            },
            timeout=120,
        )
    except requests.RequestException as exc:
        raise QAProviderError(f"{resolve_provider_label(normalized_provider)} 请求失败：{exc}") from exc

    if response.status_code >= 400:
        detail = clean_whitespace(response.text)[:240] or f"HTTP {response.status_code}"
        raise QAProviderError(f"{resolve_provider_label(normalized_provider)} 调用失败：{detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise QAProviderError(f"{resolve_provider_label(normalized_provider)} 返回了无效 JSON 响应") from exc

    content = extract_message_content(payload)
    if not content:
        raise QAProviderError(f"{resolve_provider_label(normalized_provider)} 未返回有效回答内容")
    return content


class QASystem:
    """基于视频字幕与摘要的轻量 RAG 问答系统。"""

    def __init__(self, *, video=None):
        self.video = video
        self._chunks = self._build_knowledge_chunks(video)

    def has_context(self) -> bool:
        return bool(self._chunks)

    def _build_knowledge_chunks(self, video) -> list[KnowledgeChunk]:
        if video is None:
            return []

        chunks: list[KnowledgeChunk] = []
        order = 1

        summary = clean_multiline_text(getattr(video, "summary", "") or "")
        if summary:
            chunks.append(
                KnowledgeChunk(
                    text=summary,
                    source_type="summary",
                    source_label="视频摘要",
                    order=order,
                    boost=0.45,
                )
            )
            order += 1

        tags = parse_tags(getattr(video, "tags", "") or "")
        if tags:
            chunks.append(
                KnowledgeChunk(
                    text="、".join(tags),
                    source_type="tags",
                    source_label="视频标签",
                    order=order,
                    boost=0.2,
                )
            )
            order += 1

        subtitle_rows = [
            {
                "start_time": getattr(row, "start_time", 0.0),
                "end_time": getattr(row, "end_time", 0.0),
                "text": getattr(row, "text", ""),
            }
            for row in (getattr(video, "subtitles", None) or [])
            if clean_whitespace(getattr(row, "text", ""))
        ]
        if not subtitle_rows:
            subtitle_rows = parse_srt_chunks(getattr(video, "subtitle_filepath", "") or "")

        for chunk in build_subtitle_chunks(subtitle_rows):
            chunk.order = order
            chunks.append(chunk)
            order += 1

        return chunks

    def _call_model_stream(
        self,
        messages: list[dict],
        *,
        provider: str,
        model: str,
        deep_thinking: bool,
    ) -> Generator[tuple[str, str, str], None, None]:
        """流式调用模型，yield (thinking_chunk, answer_chunk, provider_info)。

        - deep_thinking=True: 强制使用 DeepSeek Reasoner 流式调用
        - deep_thinking=False: 非流式（复用水印逻辑）
        """
        if deep_thinking:
            resolved_provider = provider or "deepseek"
            resolved_model = model or resolve_model(resolved_provider, "", deep_thinking=True)
            logger.info("深度思考流式调用: provider=%s, model=%s", resolved_provider, resolved_model)
            yield "", "", f"{resolved_provider}:{resolved_model}"
            for thinking_chunk, answer_chunk in call_deepseek_reasoner_stream(messages, model=resolved_model):
                yield thinking_chunk, answer_chunk, ""
            return

        primary_provider = "qwen"
        primary_model = model or resolve_model(primary_provider, "", deep_thinking=False)
        try:
            answer = call_provider_chat(messages, provider=primary_provider, model=primary_model)
            yield "", answer, f"{primary_provider}:{primary_model}"
            return
        except Exception as primary_error:
            logger.warning("直接回答模式-主模型(%s)调用失败，触发兜底: %s", primary_model, primary_error)

        fallback_provider = "deepseek"
        fallback_model = resolve_model(fallback_provider, "deepseek-chat", deep_thinking=False)
        logger.info("直接回答模式-兜底模型: provider=%s, model=%s", fallback_provider, fallback_model)
        answer = call_provider_chat(messages, provider=fallback_provider, model=fallback_model)
        yield "", answer, f"{fallback_provider}:{fallback_model}"

    def _build_result(self, answer: str, provider: str, model: str, references: list[KnowledgeChunk]) -> dict:
        normalized_provider = normalize_provider(provider, model)
        time_ordered_subtitles = [c for c in references if c.source_type == "subtitle"]
        time_ordered_subtitles.sort(key=lambda c: float(c.start_time or 0))
        subtitle_time_order = {id(c): i + 1 for i, c in enumerate(time_ordered_subtitles)}
        return {
            "answer": clean_multiline_text(answer),
            "provider": normalized_provider,
            "model": model,
            "provider_label": resolve_provider_label(normalized_provider),
            "references": [
                chunk.to_reference(index, time_order=subtitle_time_order.get(id(chunk)))
                for index, chunk in enumerate(references, start=1)
            ],
        }

    def ask(
        self,
        question: str,
        *,
        provider: str = "",
        model: str = "",
        deep_thinking: bool = False,
        mode: str = "video",
        history: Optional[list] = None,
    ) -> dict:
        """非流式问答接口（保留向后兼容）。"""
        normalized_provider = normalize_provider(provider, model)
        history_messages = normalize_history_messages(history)

        if mode == "video":
            if not self._chunks:
                raise QAConfigError("该视频暂无可用于问答的字幕或摘要内容")

            retrieval_query = build_retrieval_query(question, history_messages)
            ranked_chunks = rank_chunks(retrieval_query, self._chunks, top_k=max(1, int(settings.QA_TOP_K)))
            context_text = build_context_text(ranked_chunks, max_chars=max(500, int(settings.QA_MAX_CONTEXT_CHARS)))
            messages = build_video_qa_messages(
                question,
                video_title=getattr(self.video, "title", "") or "",
                context_text=context_text,
                history_messages=history_messages,
            )

            if deep_thinking:
                resolved_model = model or resolve_model(normalized_provider, "", deep_thinking=True)
                answer = call_provider_chat(messages, provider="deepseek", model=resolved_model)
                thinking = ""
                payload_answer, payload_thinking = answer, ""
                return self._build_result(payload_answer, "deepseek", resolved_model, ranked_chunks)

            primary_provider = "qwen"
            primary_model = model or resolve_model(primary_provider, "", deep_thinking=False)
            try:
                answer = call_provider_chat(messages, provider=primary_provider, model=primary_model)
                return self._build_result(answer, primary_provider, primary_model, ranked_chunks)
            except Exception as primary_error:
                logger.warning("直接回答模式-主模型(%s)调用失败，触发兜底: %s", primary_model, primary_error)

            fallback_provider = "deepseek"
            fallback_model = resolve_model(fallback_provider, "deepseek-chat", deep_thinking=False)
            answer = call_provider_chat(messages, provider=fallback_provider, model=fallback_model)
            return self._build_result(answer, fallback_provider, fallback_model, ranked_chunks)

        messages = build_free_qa_messages(question, history_messages=history_messages)
        if deep_thinking:
            resolved_model = model or resolve_model(normalized_provider, "", deep_thinking=True)
            answer = call_provider_chat(messages, provider="deepseek", model=resolved_model)
            return self._build_result(answer, "deepseek", resolved_model, [])

        primary_provider = "qwen"
        primary_model = model or resolve_model(primary_provider, "", deep_thinking=False)
        try:
            answer = call_provider_chat(messages, provider=primary_provider, model=primary_model)
            return self._build_result(answer, primary_provider, primary_model, [])
        except Exception as primary_error:
            logger.warning("直接回答模式-主模型(%s)调用失败，触发兜底: %s", primary_model, primary_error)

        fallback_provider = "deepseek"
        fallback_model = resolve_model(fallback_provider, "deepseek-chat", deep_thinking=False)
        answer = call_provider_chat(messages, provider=fallback_provider, model=fallback_model)
        return self._build_result(answer, fallback_provider, fallback_model, [])

    def answer_stream(
        self,
        question: str,
        *,
        provider: str = "",
        model: str = "",
        deep_thinking: bool = False,
        mode: str = "video",
        history: Optional[list] = None,
    ) -> Generator[dict, None, None]:
        normalized_provider = normalize_provider(provider, model)
        history_messages = normalize_history_messages(history)

        resolved_model = model or resolve_model(normalized_provider, model, deep_thinking=deep_thinking)

        if mode == "video":
            if not self._chunks:
                raise QAConfigError("该视频暂无可用于问答的字幕或摘要内容")

            yield build_stream_event(
                "status",
                stage="retrieving",
                message="正在检索视频字幕、摘要与标签",
                progress=25,
                provider=normalized_provider,
                provider_label=resolve_provider_label(normalized_provider),
                model=resolved_model,
            )
            retrieval_query = build_retrieval_query(question, history_messages)
            ranked_chunks = rank_chunks(retrieval_query, self._chunks, top_k=max(1, int(settings.QA_TOP_K)))
            context_text = build_context_text(ranked_chunks, max_chars=max(500, int(settings.QA_MAX_CONTEXT_CHARS)))
            messages = build_video_qa_messages(
                question,
                video_title=getattr(self.video, "title", "") or "",
                context_text=context_text,
                history_messages=history_messages,
            )

            if deep_thinking:
                yield build_stream_event(
                    "status",
                    stage="reasoning",
                    message="正在深度思考...",
                    progress=50,
                    provider="deepseek",
                    provider_label="DeepSeek",
                    model=resolved_model,
                )
                thinking_buffer = ""
                answer_buffer = ""
                provider_info = ""
                for thinking_chunk, answer_chunk, provider_info in self._call_model_stream(
                    messages, provider=normalized_provider, model=model, deep_thinking=True
                ):
                    if thinking_chunk:
                        thinking_buffer += thinking_chunk
                        yield build_stream_event(
                            "thinking",
                            stage="streaming",
                            thinking=thinking_chunk,
                            delta=thinking_chunk,
                            progress=52,
                        )
                    if answer_chunk:
                        answer_buffer += answer_chunk

                if not thinking_buffer and not answer_buffer:
                    raise QAProviderError("DeepSeek 深度思考未返回任何内容")

                provider_label, final_model = resolve_provider_label("deepseek"), resolved_model
                if ":" in provider_info:
                    pv, pv_model = provider_info.split(":", 1)
                    provider_label = resolve_provider_label(pv)
                    final_model = pv_model

                yield build_stream_event(
                    "thinking_complete",
                    stage="completed",
                    thinking=thinking_buffer,
                    progress=65,
                    provider="deepseek",
                    provider_label=provider_label,
                    model=final_model,
                )

                yield build_stream_event(
                    "status",
                    stage="organizing",
                    message="正在整理回答与引用片段",
                    progress=80,
                    provider="deepseek",
                    provider_label=provider_label,
                    model=final_model,
                )
                result = self._build_result(answer_buffer, "deepseek", final_model, ranked_chunks)
                yield build_stream_event(
                    "answer",
                    stage="completed",
                    message="回答已完成",
                    progress=100,
                    answer=result["answer"],
                    provider=result["provider"],
                    provider_label=result["provider_label"],
                    model=result["model"],
                    references=result["references"],
                )
                return

            answering_stage, answering_message = resolve_answering_status(normalized_provider, deep_thinking=False)
            yield build_stream_event(
                "status",
                stage=answering_stage,
                message=answering_message,
                progress=45,
                provider=normalized_provider,
                provider_label=resolve_provider_label(normalized_provider),
                model=resolved_model,
            )
            answer_buffer = ""
            provider_info = ""
            for thinking_chunk, answer_chunk, provider_info in self._call_model_stream(
                messages, provider=normalized_provider, model=model, deep_thinking=False
            ):
                answer_buffer += answer_chunk

            provider_label, final_model = resolve_provider_label(normalized_provider), resolved_model
            if ":" in provider_info:
                pv, pv_model = provider_info.split(":", 1)
                provider_label = resolve_provider_label(pv)
                final_model = pv_model

            result = self._build_result(answer_buffer, normalized_provider, final_model, ranked_chunks)
            yield build_stream_event(
                "status",
                stage="organizing",
                message="正在整理回答与引用片段",
                progress=85,
                provider=result["provider"],
                provider_label=result["provider_label"],
                model=result["model"],
            )
            yield build_stream_event(
                "answer",
                stage="completed",
                message="回答已完成",
                progress=100,
                answer=result["answer"],
                provider=result["provider"],
                provider_label=result["provider_label"],
                model=result["model"],
                references=result["references"],
            )
            return

        # free 模式
        messages = build_free_qa_messages(question, history_messages=history_messages)

        if deep_thinking:
            yield build_stream_event(
                "status",
                stage="reasoning",
                message="正在深度思考...",
                progress=50,
                provider="deepseek",
                provider_label="DeepSeek",
                model=resolved_model,
            )
            thinking_buffer = ""
            answer_buffer = ""
            provider_info = ""
            for thinking_chunk, answer_chunk, provider_info in self._call_model_stream(
                messages, provider=normalized_provider, model=model, deep_thinking=True
            ):
                if thinking_chunk:
                    thinking_buffer += thinking_chunk
                    yield build_stream_event(
                        "thinking",
                        stage="streaming",
                        thinking=thinking_chunk,
                        delta=thinking_chunk,
                        progress=52,
                    )
                if answer_chunk:
                    answer_buffer += answer_chunk

            if not thinking_buffer and not answer_buffer:
                raise QAProviderError("DeepSeek 深度思考未返回任何内容")

            provider_label, final_model = resolve_provider_label("deepseek"), resolved_model
            if ":" in provider_info:
                pv, pv_model = provider_info.split(":", 1)
                provider_label = resolve_provider_label(pv)
                final_model = pv_model

            yield build_stream_event(
                "thinking_complete",
                stage="completed",
                thinking=thinking_buffer,
                progress=65,
                provider="deepseek",
                provider_label=provider_label,
                model=final_model,
            )
        else:
            answering_stage, answering_message = resolve_answering_status(normalized_provider, deep_thinking=False)
            yield build_stream_event(
                "status",
                stage=answering_stage,
                message=answering_message,
                progress=45,
                provider=normalized_provider,
                provider_label=resolve_provider_label(normalized_provider),
                model=resolved_model,
            )
            answer_buffer = ""
            provider_info = ""
            for thinking_chunk, answer_chunk, provider_info in self._call_model_stream(
                messages, provider=normalized_provider, model=model, deep_thinking=False
            ):
                answer_buffer += answer_chunk

            provider_label, final_model = resolve_provider_label(normalized_provider), resolved_model
            if ":" in provider_info:
                pv, pv_model = provider_info.split(":", 1)
                provider_label = resolve_provider_label(pv)
                final_model = pv_model

        result = self._build_result(
            answer_buffer, "deepseek" if deep_thinking else normalized_provider, final_model, []
        )
        yield build_stream_event(
            "status",
            stage="organizing",
            message="正在整理回答",
            progress=85,
            provider=result["provider"],
            provider_label=result["provider_label"],
            model=result["model"],
        )
        yield build_stream_event(
            "answer",
            stage="completed",
            message="回答已完成",
            progress=100,
            answer=result["answer"],
            provider=result["provider"],
            provider_label=result["provider_label"],
            model=result["model"],
            references=result["references"],
        )
