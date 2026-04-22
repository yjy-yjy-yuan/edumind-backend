"""Sleek 设计能力代理路由。"""

from __future__ import annotations

import base64
import logging
from typing import Any
from typing import Optional

from app.core.database import get_db
from app.routers.auth import get_current_user_or_404
from app.schemas.design import DesignMessageRequest
from app.schemas.design import DesignProjectCreateRequest
from app.schemas.design import DesignScreenshotRequest
from app.services import sleek_service
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_authenticated(db: Session, authorization: Optional[str]) -> None:
    get_current_user_or_404(db, None, authorization)


def _normalize_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("data") if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def _normalize_single(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("data") if isinstance(payload, dict) else {}
    return item if isinstance(item, dict) else {}


def _build_screenshot_payload(project_id: str, component_ids: list[str]) -> Optional[dict[str, Any]]:
    if not component_ids:
        return None

    screenshot = sleek_service.render_screenshot(
        project_id, component_ids, image_format="png", background="transparent"
    )
    content = screenshot.get("content") or b""
    if not content:
        return None

    return {
        "component_ids": component_ids,
        "mime_type": screenshot.get("content_type", "image/png"),
        "data_base64": base64.b64encode(content).decode("ascii"),
    }


def _collect_component_ids(run_payload: dict[str, Any]) -> list[str]:
    data = _normalize_single(run_payload)
    result = data.get("result") if isinstance(data, dict) else {}
    operations = result.get("operations") if isinstance(result, dict) else []
    component_ids: list[str] = []

    for operation in operations if isinstance(operations, list) else []:
        if not isinstance(operation, dict):
            continue
        component_id = str(operation.get("componentId") or "").strip()
        op_type = str(operation.get("type") or "").strip()
        if component_id and op_type in {"screen_created", "screen_updated"} and component_id not in component_ids:
            component_ids.append(component_id)
    return component_ids


def _format_run_response(project_id: str, run_payload: dict[str, Any], *, include_screenshots: bool) -> dict[str, Any]:
    data = _normalize_single(run_payload)
    response = {
        "success": True,
        "project_id": project_id,
        "run": data,
        "screenshots": [],
    }

    if not include_screenshots:
        return response

    if str(data.get("status") or "").strip().lower() != "completed":
        return response

    component_ids = _collect_component_ids(run_payload)
    if not component_ids:
        return response

    try:
        for component_id in component_ids:
            screenshot = _build_screenshot_payload(project_id, [component_id])
            if screenshot:
                response["screenshots"].append(screenshot)

        combined = _build_screenshot_payload(project_id, component_ids)
        if combined:
            response["combined_screenshot"] = combined
    except sleek_service.SleekAPIError as exc:
        logger.warning("Sleek 截图生成失败 | project=%s | error=%s", project_id, exc)
        response["screenshot_error"] = str(exc)

    return response


@router.get("/status")
def get_design_status(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    return {
        "success": True,
        "configured": sleek_service.is_configured(),
        "provider": "sleek",
        "base_url": str(sleek_service._base_url()),
    }


@router.get("/projects")
def get_projects(
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        payload = sleek_service.list_projects(limit=limit, offset=offset)
        return {
            "success": True,
            "items": _normalize_items(payload),
            "pagination": payload.get("pagination") if isinstance(payload, dict) else None,
        }
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/projects")
def post_project(
    request: DesignProjectCreateRequest,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        payload = sleek_service.create_project(request.name)
        return {"success": True, "project": _normalize_single(payload)}
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/projects/{project_id}/components")
def get_project_components(
    project_id: str,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        payload = sleek_service.list_components(project_id, limit=limit, offset=offset)
        return {
            "success": True,
            "items": _normalize_items(payload),
            "pagination": payload.get("pagination") if isinstance(payload, dict) else None,
        }
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/projects/{project_id}/components/{component_id}")
def get_project_component(
    project_id: str,
    component_id: str,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        payload = sleek_service.get_component(project_id, component_id)
        return {"success": True, "component": _normalize_single(payload)}
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/projects/{project_id}/messages")
def post_project_message(
    project_id: str,
    request: DesignMessageRequest,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        run_payload = sleek_service.create_chat_run(
            project_id,
            message_text=request.message,
            image_urls=request.image_urls,
            target_screen_id=request.target_screen_id,
            idempotency_key=request.idempotency_key,
        )

        run = _normalize_single(run_payload)
        run_id = str(run.get("runId") or "").strip()
        if request.wait and run_id:
            run_payload = sleek_service.wait_for_chat_run(project_id, run_id)

        return _format_run_response(project_id, run_payload, include_screenshots=request.include_screenshots)
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/projects/{project_id}/runs/{run_id}")
def get_project_run(
    project_id: str,
    run_id: str,
    include_screenshots: bool = False,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        payload = sleek_service.get_chat_run(project_id, run_id)
        return _format_run_response(project_id, payload, include_screenshots=include_screenshots)
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/projects/{project_id}/screenshots")
def post_project_screenshots(
    project_id: str,
    request: DesignScreenshotRequest,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _ensure_authenticated(db, authorization)
    try:
        screenshot = sleek_service.render_screenshot(
            project_id,
            request.component_ids,
            image_format=request.format,
            scale=request.scale,
            background=request.background,
            show_dots=request.show_dots,
        )
        return {
            "success": True,
            "screenshot": {
                "component_ids": request.component_ids,
                "mime_type": screenshot.get("content_type", "image/png"),
                "data_base64": base64.b64encode(screenshot.get("content") or b"").decode("ascii"),
            },
        }
    except sleek_service.SleekConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sleek_service.SleekAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
