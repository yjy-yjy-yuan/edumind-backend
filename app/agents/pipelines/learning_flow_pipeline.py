"""学习流：Planner → Executor（仅经 governance）→ Validator。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from app.agents.budget import TokenBudget
from app.agents.exceptions import GovernanceError
from app.agents.governance.gateway import execute_tool
from app.agents.prompts.versions import LEARNING_FLOW_PROMPT_VERSION
from app.agents.prompts.versions import ORCHESTRATION_PIPELINE_VERSION
from app.core.config import settings
from app.models.note import Note
from app.models.video import Video
from app.services.learning_flow_agent import AgentContext
from app.services.learning_flow_agent import _build_note_content
from app.services.learning_flow_agent import _build_note_title
from app.services.learning_flow_agent import _build_thought_tags
from app.services.learning_flow_agent import _infer_note_category
from app.services.learning_flow_agent import _subtitle_excerpt_for_time
from app.services.learning_flow_agent import build_plan
from app.services.learning_flow_agent import infer_intent
from app.services.learning_flow_agent import normalize_user_input
from app.services.video_content_service import fallback_tags
from app.services.video_content_service import normalize_summary_style
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _validator_confirm_note(db: Session, note_id: int, video_id: int) -> bool:
    row = db.query(Note).filter(Note.id == note_id, Note.video_id == video_id).first()
    return row is not None


def run_learning_flow_pipeline(db: Session, *, request) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    budget = TokenBudget(max_tokens=int(getattr(settings, "AGENT_LEARNING_FLOW_TOKEN_BUDGET", 8000) or 8000))
    budget.charge("planner_context", 120)

    logger.debug(
        "learning_flow pipeline | trace_id=%s | video_id=%s | page_context=%s",
        trace_id,
        request.video_id,
        request.page_context,
    )

    video = None
    if request.video_id is not None:
        video = db.query(Video).filter(Video.id == request.video_id).first()
        if not video:
            raise ValueError("视频不存在")

    ctx = AgentContext(
        video=video,
        subtitle_text=str(request.subtitle_text or ""),
        current_time_seconds=request.current_time_seconds,
        recent_qa_messages=list(request.recent_qa_messages or []),
        page_context=str(request.page_context or "video_detail"),
        user_input=normalize_user_input(request.user_input),
    )

    # --- Planner（与执行上下文隔离：仅产出意图与计划）---
    intent = infer_intent(ctx.user_input)
    plan = build_plan(intent, ctx)
    budget.charge("planner_infer", 80)
    actions: list[str] = []
    action_records: list[dict[str, Any]] = []

    if video is None:
        result = {"video_id": None, "preview": ctx.user_input[:200]}
        return _finalize_response(
            intent=intent,
            plan=plan,
            actions=actions,
            result=result,
            note_id=None,
            video_id=None,
            action_records=action_records,
            trace_id=trace_id,
            budget=budget,
        )

    # --- Executor：副作用仅经 governance.execute_tool ---
    subtitle_excerpt = _subtitle_excerpt_for_time(video, ctx.current_time_seconds)
    summary_seed = subtitle_excerpt or ctx.subtitle_text.strip() or video.summary or video.title or ""

    summary_text = ""
    if summary_seed.strip():
        try:
            sum_res = execute_tool(
                "lf_generate_summary_fallback",
                {
                    "summary_seed": summary_seed,
                    "title": video.title or "",
                    "style": normalize_summary_style("study"),
                },
                db=db,
                trace_id=trace_id,
            )
            summary_text = str(sum_res.get("summary_text") or "")
            est = int(sum_res.get("tokens_estimated") or 0)
            budget.charge("executor_summary", max(est, 50))
            actions.append("summary_generated")
            action_records.append({"type": "summary_generated", "message": "已生成片段摘要", "data": {}})
        except GovernanceError:
            raise
        except Exception as exc:
            logger.exception("summary tool failed | trace_id=%s | error=%s", trace_id, exc)
            raise

    category = _infer_note_category(" ".join([subtitle_excerpt, ctx.subtitle_text, summary_text]))
    note_title = _build_note_title(video, category, ctx.current_time_seconds)
    note_content = _build_note_content(ctx, summary_text, subtitle_excerpt=subtitle_excerpt)
    if not note_content:
        note_content = summary_text or subtitle_excerpt or video.summary or video.title or "学习笔记"

    tags_joined = ",".join(_build_thought_tags(ctx, subtitle_excerpt))
    keywords_joined = ",".join(fallback_tags(note_content, title=note_title, max_tags=5))

    try:
        note_res = execute_tool(
            "lf_persist_note",
            {
                "video_id": video.id,
                "title": note_title,
                "content": note_content,
                "note_type": "text",
                "tags": tags_joined,
                "keywords": keywords_joined,
            },
            db=db,
            trace_id=trace_id,
        )
        note_id = int(note_res["note_id"])
        est = int(note_res.get("tokens_estimated") or 0)
        budget.charge("executor_persist_note", max(est, 40))
        actions.append("note_created")
        action_records.append({"type": "note_created", "message": "已创建笔记", "data": {"note_id": note_id}})
    except GovernanceError:
        raise
    except Exception as exc:
        logger.exception("persist_note failed | trace_id=%s | error=%s", trace_id, exc)
        raise

    if ctx.current_time_seconds is not None:
        try:
            ts_res = execute_tool(
                "lf_create_timestamp",
                {
                    "note_id": note_id,
                    "time_seconds": float(ctx.current_time_seconds),
                    "subtitle_text": subtitle_excerpt.strip() or ctx.subtitle_text.strip() or None,
                },
                db=db,
                trace_id=trace_id,
            )
            budget.charge("executor_timestamp", int(ts_res.get("tokens_estimated") or 8))
            actions.append("timestamp_attached")
            action_records.append(
                {
                    "type": "timestamp_attached",
                    "message": "已绑定时间戳",
                    "data": {
                        "timestamp_id": ts_res.get("timestamp_id"),
                        "time_seconds": ts_res.get("time_seconds"),
                    },
                }
            )
        except GovernanceError:
            raise
        except Exception as exc:
            logger.exception("timestamp tool failed | trace_id=%s | error=%s", trace_id, exc)
            raise

    # --- Validator：确认写库一致性 ---
    if not _validator_confirm_note(db, note_id, video.id):
        logger.error("validator failed | trace_id=%s | note_id=%s", trace_id, note_id)
        raise RuntimeError("validator: note not found after persist")

    budget.charge("validator", 24)

    result = {
        "note_id": note_id,
        "title": note_title,
        "summary": summary_text,
        "video_id": video.id,
        "category": category,
        "pipeline_meta": {
            "trace_id": trace_id,
            "orchestration": ORCHESTRATION_PIPELINE_VERSION,
            "prompt_version": LEARNING_FLOW_PROMPT_VERSION,
            "token_budget": budget.as_dict(),
        },
    }

    return _finalize_response(
        intent=intent,
        plan=plan,
        actions=actions,
        result=result,
        note_id=note_id,
        video_id=video.id,
        action_records=action_records,
        trace_id=trace_id,
        budget=budget,
    )


def _finalize_response(
    *,
    intent: str,
    plan: list[str],
    actions: list[str],
    result: dict[str, Any],
    note_id: int | None,
    video_id: int | None,
    action_records: list[dict[str, Any]],
    trace_id: str,
    budget: TokenBudget,
) -> dict[str, Any]:
    if isinstance(result, dict) and "pipeline_meta" not in result and video_id is None:
        result = {
            **result,
            "pipeline_meta": {
                "trace_id": trace_id,
                "orchestration": ORCHESTRATION_PIPELINE_VERSION,
                "prompt_version": LEARNING_FLOW_PROMPT_VERSION,
                "token_budget": budget.as_dict(),
            },
        }

    return {
        "intent": intent,
        "plan": plan,
        "actions": actions,
        "result": result,
        "note_id": note_id,
        "video_id": video_id,
        "created_at": datetime.utcnow(),
        "action_records": action_records,
    }
