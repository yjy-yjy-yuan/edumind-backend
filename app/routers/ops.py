"""运维观测路由。"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent, AnalyticsStatus
from app.core.config import settings
from app.core.database import get_db
from app.utils.auth_deps import resolve_user_from_request

router = APIRouter()


def _resolve_trace_id(request: Request) -> str:
    raw = request.headers.get("X-Trace-Id") or request.headers.get("X-Request-Id")
    if raw and str(raw).strip():
        return str(raw).strip()[:128]
    return str(uuid.uuid4())


def _attach_trace_headers(response: Response, trace_id: str) -> None:
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Request-Id"] = trace_id


@router.get("/vinci/metrics")
async def get_vinci_ops_metrics(
    request: Request,
    response: Response,
    user_id: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """返回 Vinci 观测窗口快照。"""
    trace_id = _resolve_trace_id(request)
    _attach_trace_headers(response, trace_id)
    t0 = time.perf_counter()

    user = resolve_user_from_request(db, user_id, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录后查看 Vinci 运营指标")

    metrics = get_telemetry().module_metrics("vinci")
    payload = {
        **metrics,
        "thresholds": {
            "max_failure_rate": float(getattr(settings, "ANALYTICS_ALERT_MAX_FAILURE_RATE", 0.15)),
            "max_timeout_rate": float(getattr(settings, "ANALYTICS_ALERT_MAX_TIMEOUT_RATE", 0.10)),
            "latency_timeout_ms": float(getattr(settings, "ANALYTICS_ALERT_LATENCY_TIMEOUT_MS", 30_000.0)),
            "max_p95_latency_ms": float(getattr(settings, "ANALYTICS_ALERT_MAX_P95_LATENCY_MS", 12_000.0)),
            "min_interval_sec": float(getattr(settings, "ANALYTICS_ALERT_MIN_INTERVAL_SEC", 60.0)),
        },
    }
    latency_ms = (time.perf_counter() - t0) * 1000.0
    get_telemetry().emit(
        AnalyticsEvent(
            event_type="vinci_ops_metrics_served",
            trace_id=trace_id,
            module="vinci",
            status=AnalyticsStatus.OK.value,
            latency_ms=latency_ms,
            metadata={
                "total": payload.get("total"),
                "degraded_count": payload.get("degraded_count"),
            },
        ),
        skip_alerts=True,
    )
    return payload
