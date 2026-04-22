"""学习流智能体编排服务。

第一版仅负责把用户意图收敛成时间戳笔记，不引入新的业务表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from typing import Optional

from app.models.video import Video
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    video: Optional[Video]
    subtitle_text: str
    current_time_seconds: Optional[float]
    recent_qa_messages: list[dict]
    page_context: str
    user_input: str


def normalize_user_input(text: str) -> str:
    return str(text or "").strip()


def infer_intent(user_input: str) -> str:
    _ = normalize_user_input(user_input)
    return "timestamp_note"


def build_plan(intent: str, ctx: AgentContext) -> list[str]:
    plan = ["读取视频上下文", "定位当前字幕片段", "自动判断笔记分类", "生成学习笔记"]
    if ctx.current_time_seconds is not None:
        plan.append("绑定时间戳")
    return plan


def _build_note_title(video: Optional[Video], category: str, current_time_seconds: Optional[float]) -> str:
    if current_time_seconds is not None:
        minute = int(float(current_time_seconds) // 60)
        second = int(float(current_time_seconds) % 60)
        time_text = f"{minute:02d}:{second:02d}"
    else:
        time_text = "00:00"
    return f"{category} · {time_text}"


def _subtitle_excerpt_for_time(video: Optional[Video], current_time_seconds: Optional[float]) -> str:
    if video is None or current_time_seconds is None:
        return ""

    subtitles = list(getattr(video, "subtitles", []) or [])
    if not subtitles:
        return ""

    ordered = sorted(subtitles, key=lambda item: float(getattr(item, "start_time", 0) or 0))
    target = float(current_time_seconds)
    best_index = 0
    best_distance = None
    for index, item in enumerate(ordered):
        start_time = float(getattr(item, "start_time", 0) or 0)
        end_time = float(getattr(item, "end_time", start_time) or start_time)
        midpoint = (start_time + end_time) / 2
        distance = abs(midpoint - target)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = index

    fragment_indexes = range(max(0, best_index - 1), min(len(ordered), best_index + 2))
    excerpts = []
    for index in fragment_indexes:
        fragment = ordered[index]
        text = str(getattr(fragment, "text", "") or "").strip()
        if not text:
            continue
        start_time = float(getattr(fragment, "start_time", 0) or 0)
        end_time = float(getattr(fragment, "end_time", start_time) or start_time)
        excerpts.append(f"[{start_time:.1f}-{end_time:.1f}] {text}")

    return "\n".join(excerpts).strip()


def _infer_note_category(text: str) -> str:
    normalized = normalize_user_input(text)
    if any(keyword in normalized for keyword in ["例题", "例如", "举例", "算", "计算", "解题", "题目"]):
        return "例题"
    if any(keyword in normalized for keyword in ["思考", "为什么", "如何", "怎么", "原因", "讨论", "探究"]):
        return "思考题"
    if any(keyword in normalized for keyword in ["易错", "注意", "别忘", "常见错误", "容易"]):
        return "易错点"
    if any(keyword in normalized for keyword in ["结论", "总结", "归纳", "因此", "所以"]):
        return "结论"
    return "知识点"


def _build_note_content(ctx: AgentContext, summary_text: str, subtitle_excerpt: str = "") -> str:
    parts = []
    if subtitle_excerpt.strip():
        parts.append(f"字幕片段：\n{subtitle_excerpt.strip()}")
    elif ctx.subtitle_text.strip():
        parts.append(f"字幕片段：{ctx.subtitle_text.strip()}")
    if summary_text.strip():
        parts.append(f"摘要：{summary_text.strip()}")
    return "\n".join(parts).strip()


def _build_thought_tags(ctx: AgentContext, subtitle_excerpt: str = "") -> list[str]:
    source = " ".join([subtitle_excerpt, ctx.subtitle_text])
    tags = []
    for tag, keywords in [
        ("再思考", ["再看", "回看", "思考", "继续", "复习"]),
        ("很重要", ["重要", "关键", "核心", "重点"]),
        ("待复习", ["复习", "记住", "回顾"]),
        ("需要查证", ["查证", "确认", "核对", "验证"]),
        ("想再看一遍", ["再看", "回看", "重新看"]),
    ]:
        if any(keyword in source for keyword in keywords):
            tags.append(tag)
    if not tags:
        tags.append("再思考")
    return tags[:5]


def execute_learning_flow_agent(db: Session, *, request) -> dict[str, Any]:
    """编排入口：Planner / Executor（经 governance）/ Validator。"""
    from app.agents.pipelines.learning_flow_pipeline import run_learning_flow_pipeline

    logger.debug(
        "agent request | video_id=%s | page_context=%s | current_time_seconds=%s | subtitle_len=%s | qa_messages=%s | user_input=%s",
        request.video_id,
        request.page_context,
        request.current_time_seconds,
        len(str(request.subtitle_text or "")),
        len(request.recent_qa_messages or []),
        normalize_user_input(request.user_input),
    )
    return run_learning_flow_pipeline(db, request=request)
