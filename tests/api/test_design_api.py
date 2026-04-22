"""设计助手 API 测试。"""

import base64

import pytest
from app.utils.auth_token import build_auth_token


def _auth_headers(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {build_auth_token(user_id)}"}


@pytest.mark.api
def test_get_design_status_requires_auth(client):
    response = client.get("/api/design/status")
    assert response.status_code == 401


@pytest.mark.api
def test_list_design_projects_returns_service_payload(client, sample_user, monkeypatch):
    monkeypatch.setattr("app.services.sleek_service.is_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.sleek_service.list_projects",
        lambda limit=50, offset=0: {
            "data": [{"id": "proj_123", "name": "EduMind Design"}],
            "pagination": {"total": 1, "limit": limit, "offset": offset},
        },
    )

    response = client.get("/api/design/projects", headers=_auth_headers(sample_user.id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["items"][0]["id"] == "proj_123"
    assert payload["pagination"]["total"] == 1


@pytest.mark.api
def test_create_design_project(client, sample_user, monkeypatch):
    monkeypatch.setattr(
        "app.services.sleek_service.create_project",
        lambda name: {"data": {"id": "proj_new", "name": name}},
    )

    response = client.post(
        "/api/design/projects",
        headers=_auth_headers(sample_user.id),
        json={"name": "EduMind Mobile Concepts"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["project"]["name"] == "EduMind Mobile Concepts"


@pytest.mark.api
def test_send_design_message_includes_screenshot_preview(client, sample_user, monkeypatch):
    monkeypatch.setattr(
        "app.services.sleek_service.create_chat_run",
        lambda *args, **kwargs: {"data": {"runId": "run_123", "status": "queued", "statusUrl": "/status"}},
    )
    monkeypatch.setattr(
        "app.services.sleek_service.wait_for_chat_run",
        lambda project_id, run_id: {
            "data": {
                "runId": run_id,
                "status": "completed",
                "result": {
                    "assistantText": "我已经生成了新的首页与推荐页。",
                    "operations": [
                        {
                            "type": "screen_created",
                            "screenId": "scr_home",
                            "screenName": "Home",
                            "componentId": "cmp_home",
                        }
                    ],
                },
            }
        },
    )
    monkeypatch.setattr(
        "app.services.sleek_service.render_screenshot",
        lambda project_id, component_ids, **kwargs: {
            "content": b"fake-image",
            "content_type": "image/png",
        },
    )

    response = client.post(
        "/api/design/projects/proj_123/messages",
        headers=_auth_headers(sample_user.id),
        json={
            "message": "做一个更适合 EduMind 的学习首页和推荐页",
            "wait": True,
            "include_screenshots": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["status"] == "completed"
    assert payload["screenshots"][0]["component_ids"] == ["cmp_home"]
    assert base64.b64decode(payload["screenshots"][0]["data_base64"]) == b"fake-image"


@pytest.mark.api
def test_get_component_detail_returns_html_code(client, sample_user, monkeypatch):
    monkeypatch.setattr(
        "app.services.sleek_service.get_component",
        lambda project_id, component_id: {
            "data": {
                "id": component_id,
                "name": "Home",
                "versions": [{"id": "ver_1", "version": 1, "code": "<!DOCTYPE html><html></html>"}],
            }
        },
    )

    response = client.get(
        "/api/design/projects/proj_123/components/cmp_home",
        headers=_auth_headers(sample_user.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["component"]["id"] == "cmp_home"
    assert "<!DOCTYPE html>" in payload["component"]["versions"][0]["code"]
