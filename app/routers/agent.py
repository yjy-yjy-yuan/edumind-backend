"""学习流智能体路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents.exceptions import GovernanceError
from app.core.database import get_db
from app.schemas.agent import AgentExecuteRequest, AgentPlanResponse
from app.services.learning_flow_agent import execute_learning_flow_agent

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_governance_error_payload(raw_detail: str) -> dict:
    detail = str(raw_detail or "").strip()
    return {
        "detail": detail or "governance_rejected",
        "error_code": "GOVERNANCE_REJECTED",
        "message": "请求未通过治理校验，请调整输入后重试。",
        "suggestion": "请简化输入内容，避免超长参数或未授权动作；如多次失败请联系管理员排查策略。",
        "recoverable": True,
    }


@router.post("/execute", response_model=AgentPlanResponse)
async def execute_agent(request: AgentExecuteRequest, db: Session = Depends(get_db)):
    try:
        payload = execute_learning_flow_agent(db, request=request)
        return payload
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=_build_governance_error_payload(str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("学习流智能体执行失败 | error=%s", exc)
        raise HTTPException(status_code=500, detail="学习流智能体执行失败，请稍后重试") from exc
