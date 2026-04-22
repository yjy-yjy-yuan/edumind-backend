"""学习流智能体路由。"""

from __future__ import annotations

import logging

from app.agents.exceptions import GovernanceError
from app.core.database import get_db
from app.schemas.agent import AgentExecuteRequest
from app.schemas.agent import AgentPlanResponse
from app.services.learning_flow_agent import execute_learning_flow_agent
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/execute", response_model=AgentPlanResponse)
async def execute_agent(request: AgentExecuteRequest, db: Session = Depends(get_db)):
    try:
        payload = execute_learning_flow_agent(db, request=request)
        return payload
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("学习流智能体执行失败 | error=%s", exc)
        raise HTTPException(status_code=500, detail="学习流智能体执行失败，请稍后重试") from exc
