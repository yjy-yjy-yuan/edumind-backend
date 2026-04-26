from __future__ import annotations

import httpx

from app.services.vinci_client import VinciClient


class _FakeClient:
    def __init__(self, post_handler):
        self._post_handler = post_handler

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, headers=None):
        return self._post_handler(url, json or {}, headers or {})


def test_request_chat_supports_official_vinci_internvl_payload(monkeypatch):
    captured = {}

    def _post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "answer": "ok",
                "history": [["q", "a"]],
                "session_id": payload.get("session_id", ""),
            },
            request=request,
        )

    monkeypatch.setattr(
        "app.services.vinci_client.httpx.Client",
        lambda timeout=None: _FakeClient(_post),
    )

    client = VinciClient(
        base_url="http://127.0.0.1:18081",
        chat_path="/api/v1/inference/internvl",
    )

    data = client.request_chat(
        prompt="what is on screen",
        history=[{"role": "user", "content": "hi"}],
        session_id="s1",
        trace_id="t1",
    )

    assert captured["url"] == "http://127.0.0.1:18081/api/v1/inference/internvl"
    assert captured["payload"]["question"] == "what is on screen"
    assert captured["payload"]["session_id"] == "s1"
    assert "base64_frames" in captured["payload"]
    assert data["answer"] == "ok"


def test_request_chat_fallbacks_to_official_vinci_endpoint_when_default_unreachable(monkeypatch):
    calls = []

    def _post(url, payload, headers):
        calls.append(url)
        request = httpx.Request("POST", url)
        if url.endswith("/api/v1/chat"):
            raise httpx.ConnectError("connection refused", request=request)
        if url.endswith("/api/v1/inference/internvl") and url.startswith("http://127.0.0.1:18081"):
            return httpx.Response(
                200,
                json={
                    "answer": "fallback ok",
                    "history": [],
                    "session_id": payload.get("session_id", ""),
                },
                request=request,
            )
        return httpx.Response(404, json={"detail": "not found"}, request=request)

    monkeypatch.setattr(
        "app.services.vinci_client.httpx.Client",
        lambda timeout=None: _FakeClient(_post),
    )

    client = VinciClient(
        base_url="http://127.0.0.1:8010",
        chat_path="/api/v1/chat",
    )

    data = client.request_chat(
        prompt="describe",
        history=[],
        session_id="s2",
        trace_id="t2",
    )

    assert data["answer"] == "fallback ok"
    assert "http://127.0.0.1:8010/api/v1/chat" in calls
    assert "http://127.0.0.1:18081/api/v1/inference/internvl" in calls
