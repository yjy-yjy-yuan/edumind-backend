"""统一工具执行网关：白名单 + 参数校验 + 审计；模型/路由不得直接写库。"""

from __future__ import annotations

import logging
import re
from typing import Any
from typing import Callable
from typing import Dict

from app.agents.exceptions import GovernanceError
from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent
from app.analytics.schema import AnalyticsStatus
from app.core.config import settings
from app.services.video_content_service import SUPPORTED_SUMMARY_STYLES
from sqlalchemy.orm import Session

from . import tools_learning_flow

logger = logging.getLogger(__name__)

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$", re.I)

# 与工具实现、DB 列宽对齐的硬边界
MAX_SUMMARY_SEED_CHARS = 120_000
MAX_NOTE_TITLE_CHARS = 500
MAX_NOTE_CONTENT_CHARS = 50_000
MAX_NOTE_TAGS_CHARS = 2_000
MAX_NOTE_KEYWORDS_CHARS = 2_000
MAX_TIMESTAMP_SUBTITLE_CHARS = 2_000
ALLOWED_NOTE_TYPES = frozenset({"text", "code", "list"})


_TOOL_HANDLERS: Dict[str, Callable[[Session, dict[str, Any]], dict[str, Any]]] = {
    "lf_generate_summary_fallback": tools_learning_flow.tool_lf_generate_summary_fallback,
    "lf_persist_note": tools_learning_flow.tool_lf_persist_note,
    "lf_create_timestamp": tools_learning_flow.tool_lf_create_timestamp,
}


def _emit_audit(*, trace_id: str, event_type: str, status: str, metadata: dict[str, Any]) -> None:
    if not getattr(settings, "AGENT_GOVERNANCE_AUDIT_ENABLED", True):
        return
    tid = (trace_id or "").strip()[:128] or settings.ANALYTICS_TRACE_ID_PLACEHOLDER
    try:
        get_telemetry().emit(
            AnalyticsEvent(
                event_type=event_type,
                trace_id=tid,
                module="agent_governance",
                status=status,
                metadata=dict(metadata or {}),
            )
        )
    except Exception:
        logger.debug("agent governance audit emit skipped", exc_info=True)


def _validate_tool_name(name: str) -> None:
    if not name or not _TOOL_NAME_RE.match(name):
        raise GovernanceError("invalid_tool_name")


def _validate_params(tool_name: str, params: dict[str, Any]) -> None:
    if not isinstance(params, dict):
        raise GovernanceError("params_must_be_object")

    if tool_name == "lf_generate_summary_fallback":
        if "summary_seed" not in params:
            raise GovernanceError("missing_summary_seed")
        seed = str(params.get("summary_seed") or "")
        if len(seed) > MAX_SUMMARY_SEED_CHARS:
            raise GovernanceError("summary_seed_too_long")
        title = str(params.get("title") or "")
        if len(title) > 512:
            raise GovernanceError("title_too_long")
        style_raw = str(params.get("style") or "").strip().lower()
        if style_raw and style_raw not in SUPPORTED_SUMMARY_STYLES:
            raise GovernanceError("invalid_summary_style")
        return

    if tool_name == "lf_persist_note":
        vid = params.get("video_id")
        if not isinstance(vid, int) or vid < 1:
            raise GovernanceError("invalid_video_id")
        title = str(params.get("title") or "").strip()
        if not title:
            raise GovernanceError("missing_title")
        if len(title) > MAX_NOTE_TITLE_CHARS:
            raise GovernanceError("title_too_long")
        content = str(params.get("content") or "").strip()
        if not content:
            raise GovernanceError("missing_content")
        if len(content) > MAX_NOTE_CONTENT_CHARS:
            raise GovernanceError("content_too_long")
        nt = str(params.get("note_type") or "text").strip().lower()
        if nt not in ALLOWED_NOTE_TYPES:
            raise GovernanceError("invalid_note_type")
        tags = str(params.get("tags") or "")
        if len(tags) > MAX_NOTE_TAGS_CHARS:
            raise GovernanceError("tags_too_long")
        keywords = str(params.get("keywords") or "")
        if len(keywords) > MAX_NOTE_KEYWORDS_CHARS:
            raise GovernanceError("keywords_too_long")
        return

    if tool_name == "lf_create_timestamp":
        nid = params.get("note_id")
        if not isinstance(nid, int) or nid < 1:
            raise GovernanceError("invalid_note_id")
        ts = params.get("time_seconds")
        if not isinstance(ts, (int, float)) or float(ts) < 0:
            raise GovernanceError("invalid_time_seconds")
        st = params.get("subtitle_text")
        if st is not None and len(str(st)) > MAX_TIMESTAMP_SUBTITLE_CHARS:
            raise GovernanceError("subtitle_text_too_long")
        return

    raise GovernanceError("unknown_tool_validation")


def execute_tool(
    tool_name: str,
    params: dict[str, Any],
    *,
    db: Session,
    trace_id: str,
    pipeline: str = "learning_flow",
) -> dict[str, Any]:
    """唯一合法工具执行入口。未注册工具一律拒绝并审计。"""
    _validate_tool_name(tool_name)
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        _emit_audit(
            trace_id=trace_id,
            event_type="agent_tool_denied",
            status=AnalyticsStatus.ERROR.value,
            metadata={"tool": tool_name, "reason": "not_whitelisted", "pipeline": pipeline},
        )
        raise GovernanceError(f"tool_not_allowed:{tool_name}")

    try:
        _validate_params(tool_name, params)
    except GovernanceError as exc:
        _emit_audit(
            trace_id=trace_id,
            event_type="agent_tool_param_invalid",
            status=AnalyticsStatus.ERROR.value,
            metadata={"tool": tool_name, "error": str(exc), "pipeline": pipeline},
        )
        raise

    _emit_audit(
        trace_id=trace_id,
        event_type="agent_tool_started",
        status=AnalyticsStatus.STARTED.value,
        metadata={"tool": tool_name, "pipeline": pipeline},
    )

    try:
        result = handler(db, params)
    except Exception as exc:
        _emit_audit(
            trace_id=trace_id,
            event_type="agent_tool_failed",
            status=AnalyticsStatus.ERROR.value,
            metadata={"tool": tool_name, "error": str(exc)[:500], "pipeline": pipeline},
        )
        raise

    _emit_audit(
        trace_id=trace_id,
        event_type="agent_tool_completed",
        status=AnalyticsStatus.OK.value,
        metadata={"tool": tool_name, "pipeline": pipeline, "keys": sorted((result or {}).keys())},
    )
    return result or {}
