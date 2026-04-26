"""学习流：Planner → Executor（仅经 governance）→ Validator。

新增集成：
- Trajectory Recorder：系统级记录每个 phase 的动作、工具调用、治理决策
- Skill Registry：技能激活版本注入系统提示词
- PromptEngine：Token 感知的提示词组装与历史截断
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.budget import TokenBudget
from app.agents.exceptions import GovernanceError
from app.agents.governance.gateway import execute_tool
from app.agents.prompt_engine import (
    AssembledPrompt,
    PromptEngine,
    PromptEngineConfig,
    PromptSegment,
)
from app.agents.prompt_engine import TokenBudget as PromptTokenBudget
from app.agents.prompts.versions import (
    LEARNING_FLOW_PROMPT_VERSION,
    ORCHESTRATION_PIPELINE_VERSION,
)
from app.agents.skill_registry import get_skill_registry
from app.agents.trajectory import (
    AgentTrajectoryRecorder,
    EpisodeRecord,
    StepRecord,
    ToolCall,
    get_trajectory_recorder,
)
from app.core.config import settings
from app.models.note import Note
from app.models.video import Video
from app.services.learning_flow_agent import (
    AgentContext,
    _build_note_content,
    _build_note_title,
    _build_thought_tags,
    _infer_note_category,
    _subtitle_excerpt_for_time,
    build_plan,
    infer_intent,
    normalize_user_input,
)
from app.services.video_content_service import fallback_tags, normalize_summary_style

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# PromptEngine 单例（进程内共享）
# ---------------------------------------------------------------

_prompt_engine: PromptEngine | None = None


def _get_prompt_engine() -> PromptEngine:
    global _prompt_engine
    if _prompt_engine is None:
        _prompt_engine = PromptEngine(config=PromptEngineConfig())
    return _prompt_engine


# ---------------------------------------------------------------
# Skill-aware Vinci Prompt Builder
# ---------------------------------------------------------------


def _build_vinci_prompt(
    user_input: str,
    context_fragment: str,
    history: list[dict[str, Any]],
    trace_id: str,
    user_id: int | None = None,
) -> tuple[str, int]:
    """
    使用 Skill Registry + PromptEngine 构建 Vinci 调用提示词。

    Returns:
        (assembled_prompt_text, estimated_tokens)
    """
    registry = get_skill_registry()
    user_id_hash = _safe_hash_user_id(user_id) if user_id is not None else None
    skill = registry.get_active_for_request("learning_flow", user_id_hash=user_id_hash)

    engine = _get_prompt_engine()

    # 注册 skill 片段
    segments: list[PromptSegment] = []
    if skill is not None:
        segments = skill.get_segments()

    # 构建 Token 预算
    prompt_budget = PromptTokenBudget(
        max_tokens=8000,
        used_tokens=0,
    )

    # 组装提示词（自动截断历史）
    messages_for_truncation = [
        {"role": "system", "content": ""},  # placeholder for system
    ]
    for msg in history or []:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        if role in ("user", "assistant"):
            messages_for_truncation.append({"role": role, "content": content})

    result = engine.assemble(
        segments=segments,
        variables={
            "user_input": user_input,
            "context_fragment": context_fragment,
        },
        messages=messages_for_truncation,
        token_budget=prompt_budget,
        extra_system_prompt=(
            f"【当前任务】\n"
            f"用户问题：{user_input}\n"
            f"上下文片段：{context_fragment}\n"
            f"请给出精炼学习摘要，保留核心概念与关键结论。"
        ),
    )

    return result.text, result.token_count


# ---------------------------------------------------------------
# Validator（数据库一致性确认）
# ---------------------------------------------------------------


def _validator_confirm_note(db: Session, note_id: int, video_id: int) -> bool:
    row = db.query(Note).filter(Note.id == note_id, Note.video_id == video_id).first()
    return row is not None


# ---------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------


def _should_enable_vinci_summary(ctx: AgentContext) -> bool:
    if not bool(getattr(settings, "VINCI_ENABLED", False)):
        return False
    if ctx.recent_qa_messages:
        return True
    return "vinci" in normalize_user_input(ctx.user_input).lower()


def _safe_hash_user_id(user_id: int | None) -> str:
    """对 user_id 做 SHA-256 哈希（用于轨迹记录，不暴露原始 ID）。"""
    if user_id is None:
        return ""
    import hashlib

    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


def run_learning_flow_pipeline(
    db: Session,
    *,
    request,
    user_id: int | None = None,
) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    budget = TokenBudget(max_tokens=int(getattr(settings, "AGENT_LEARNING_FLOW_TOKEN_BUDGET", 8000) or 8000))
    budget.charge("planner_context", 120)

    logger.debug(
        "learning_flow pipeline | trace_id=%s | video_id=%s | page_context=%s",
        trace_id,
        request.video_id,
        request.page_context,
    )

    # ---- Trajectory Recording Setup ----
    recorder: AgentTrajectoryRecorder = get_trajectory_recorder()
    skill_version = ""
    try:
        registry = get_skill_registry()
        ver = registry.get_version("learning_flow")
        skill_version = ver or ""
    except Exception:
        pass

    episode: EpisodeRecord = recorder.start_episode(
        episode_id=f"lf_{trace_id[:12]}",
        pipeline="learning_flow",
        pipeline_version=ORCHESTRATION_PIPELINE_VERSION,
        skill_version=skill_version,
        trace_id=trace_id,
        user_id_hash=_safe_hash_user_id(user_id),
        video_id=request.video_id,
        metadata={
            "page_context": str(request.page_context or "video_detail"),
            "vinci_enabled": bool(getattr(settings, "VINCI_ENABLED", False)),
        },
    )

    video = None
    if request.video_id is not None:
        video = db.query(Video).filter(Video.id == request.video_id).first()
        if not video:
            recorder.cancel_episode(episode, reason="video_not_found")
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
    planner_started = time.monotonic()
    intent = infer_intent(ctx.user_input)
    plan = build_plan(intent, ctx)
    budget.charge("planner_infer", 80)
    planner_latency_ms = (time.monotonic() - planner_started) * 1000

    recorder.record_step(
        episode,
        phase="planner",
        action={
            "intent": intent,
            "plan": plan,
            "user_input": ctx.user_input,
        },
        agent_state_before={"page_context": ctx.page_context},
        agent_state_after={"intent": intent, "plan": plan},
        latency_ms=planner_latency_ms,
    )

    actions: list[str] = []
    action_records: list[dict[str, Any]] = []

    if video is None:
        recorder.finish_episode(
            episode,
            status="completed",
            final_result={"video_id": None, "preview": ctx.user_input[:200]},
        )
        return _finalize_response(
            intent=intent,
            plan=plan,
            actions=actions,
            result={"video_id": None, "preview": ctx.user_input[:200]},
            note_id=None,
            video_id=None,
            action_records=action_records,
            trace_id=trace_id,
            budget=budget,
            episode_id=episode.episode_id,
        )

    # --- Executor：副作用仅经 governance.execute_tool ---
    subtitle_excerpt = _subtitle_excerpt_for_time(video, ctx.current_time_seconds)
    summary_seed = subtitle_excerpt or ctx.subtitle_text.strip() or video.summary or video.title or ""

    summary_text = ""
    executor_started = time.monotonic()

    if summary_seed.strip() and _should_enable_vinci_summary(ctx):
        try:
            # ---- Skill-aware Vinci prompt assembly ----
            vinci_prompt, vinci_tokens = _build_vinci_prompt(
                user_input=ctx.user_input,
                context_fragment=summary_seed,
                history=list(ctx.recent_qa_messages or []),
                trace_id=trace_id,
                user_id=user_id,
            )

            vinci_res = execute_tool(
                "lf_vinci_chat",
                {
                    "prompt": vinci_prompt,
                    "session_id": f"lf_{video.id}_{trace_id[:12]}",
                    "history": list(ctx.recent_qa_messages or []),
                },
                db=db,
                trace_id=trace_id,
            )
            budget.charge(
                "executor_vinci_chat",
                max(int(vinci_res.get("tokens_estimated") or 0), 60),
            )

            if bool(vinci_res.get("degraded")):
                actions.append("vinci_degraded_fallback")
                action_records.append(
                    {
                        "type": "vinci_degraded_fallback",
                        "message": "Vinci 降级，已自动回退本地摘要工具",
                        "data": {"error_code": vinci_res.get("error_code")},
                    }
                )
                recorder.add_tool_call_to_last_step(
                    episode,
                    name="lf_vinci_chat",
                    result=vinci_res,
                    governed=True,
                )
            else:
                summary_text = str(vinci_res.get("answer") or "").strip()
                if summary_text:
                    actions.append("vinci_summary_generated")
                    action_records.append(
                        {
                            "type": "vinci_summary_generated",
                            "message": "已通过 Vinci 生成学习摘要",
                            "data": {
                                "session_id": vinci_res.get("session_id"),
                                "history_count": len(vinci_res.get("history") or []),
                            },
                        }
                    )
                    recorder.add_tool_call_to_last_step(
                        episode,
                        name="lf_vinci_chat",
                        result=vinci_res,
                        governed=True,
                    )
        except GovernanceError as exc:
            err = str(exc or "").strip()
            if err.startswith("vinci_call_failed:"):
                error_code = err.split(":", 1)[1] or "VINCI_CALL_FAILED"
                actions.append("vinci_degraded_fallback")
                action_records.append(
                    {
                        "type": "vinci_degraded_fallback",
                        "message": "Vinci 异常，已自动回退本地摘要工具",
                        "data": {"error_code": error_code},
                    }
                )
                recorder.update_step_governance(
                    episode,
                    len(episode.steps) - 1,
                    {
                        "decision": "blocked",
                        "reason": err,
                        "error_code": error_code,
                    },
                )
                logger.warning(
                    "vinci governance failure, fallback to local summary | trace_id=%s | error=%s",
                    trace_id,
                    err,
                )
            else:
                recorder.finish_episode(episode, status="failed", error=str(exc))
                raise
        except Exception as exc:
            logger.warning(
                "vinci summary failed, fallback to local summary | trace_id=%s | error=%s",
                trace_id,
                exc,
            )
            recorder.add_tool_call_to_last_step(
                episode,
                name="lf_vinci_chat",
                error=str(exc),
                governed=True,
            )

    if summary_seed.strip() and not summary_text:
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
            recorder.add_tool_call_to_last_step(
                episode,
                name="lf_generate_summary_fallback",
                params={"style": "study"},
                result=sum_res,
                governed=True,
            )
        except GovernanceError:
            recorder.finish_episode(episode, status="failed", error="governance_error in summary")
            raise
        except Exception as exc:
            logger.exception("summary tool failed | trace_id=%s | error=%s", trace_id, exc)
            recorder.finish_episode(episode, status="failed", error=str(exc))
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
        action_records.append(
            {
                "type": "note_created",
                "message": "已创建笔记",
                "data": {"note_id": note_id},
            }
        )
        recorder.add_tool_call_to_last_step(
            episode,
            name="lf_persist_note",
            params={"video_id": video.id, "note_type": "text"},
            result=note_res,
            governed=True,
        )
    except GovernanceError:
        recorder.finish_episode(episode, status="failed", error="governance_error in persist_note")
        raise
    except Exception as exc:
        logger.exception("persist_note failed | trace_id=%s | error=%s", trace_id, exc)
        recorder.finish_episode(episode, status="failed", error=str(exc))
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
            recorder.add_tool_call_to_last_step(
                episode,
                name="lf_create_timestamp",
                params={"time_seconds": float(ctx.current_time_seconds)},
                result=ts_res,
                governed=True,
            )
        except GovernanceError:
            recorder.finish_episode(episode, status="failed", error="governance_error in create_timestamp")
            raise
        except Exception as exc:
            logger.exception("timestamp tool failed | trace_id=%s | error=%s", trace_id, exc)
            recorder.finish_episode(episode, status="failed", error=str(exc))
            raise

    # --- Validator：确认写库一致性 ---
    validator_started = time.monotonic()
    validator_passed = _validator_confirm_note(db, note_id, video.id)
    validator_latency_ms = (time.monotonic() - validator_started) * 1000

    recorder.record_step(
        episode,
        phase="validator",
        action={"operation": "confirm_note", "note_id": note_id, "video_id": video.id},
        agent_state_before={"note_id": note_id},
        agent_state_after={"validator_passed": validator_passed},
        latency_ms=validator_latency_ms,
        validation_result={"passed": validator_passed},
    )

    if not validator_passed:
        logger.error("validator failed | trace_id=%s | note_id=%s", trace_id, note_id)
        recorder.finish_episode(episode, status="failed", error="validator: note not found after persist")
        raise RuntimeError("validator: note not found after persist")

    budget.charge("validator", 24)
    executor_latency_ms = (time.monotonic() - executor_started) * 1000

    # Record executor step (aggregate all tool calls)
    recorder.record_step(
        episode,
        phase="executor",
        action={"tool_chain": actions, "summary_length": len(summary_text)},
        agent_state_before={"video_id": video.id, "subtitle_excerpt_len": len(subtitle_excerpt)},
        agent_state_after={"note_id": note_id, "summary_generated": bool(summary_text)},
        latency_ms=executor_latency_ms,
    )

    result = {
        "note_id": note_id,
        "title": note_title,
        "summary": summary_text,
        "video_id": video.id,
        "category": category,
        "pipeline_meta": {
            "trace_id": trace_id,
            "episode_id": episode.episode_id,
            "orchestration": ORCHESTRATION_PIPELINE_VERSION,
            "prompt_version": LEARNING_FLOW_PROMPT_VERSION,
            "skill_version": skill_version,
            "token_budget": budget.as_dict(),
        },
    }

    recorder.finish_episode(
        episode,
        status="completed",
        final_result={
            "note_id": note_id,
            "video_id": video.id,
            "actions": actions,
        },
    )

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
        episode_id=episode.episode_id,
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
    episode_id: str = "",
) -> dict[str, Any]:
    if isinstance(result, dict) and "pipeline_meta" not in result and video_id is None:
        result = {
            **result,
            "pipeline_meta": {
                "trace_id": trace_id,
                "episode_id": episode_id,
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
        "episode_id": episode_id,
    }
