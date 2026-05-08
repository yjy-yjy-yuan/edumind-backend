"""Qwen3-VL realtime client tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.qwen3vl_realtime_client import Qwen3VLRealtimeClient


class _QwenHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "status": "ok",
            "loaded": False,
            "model": "test-model",
            "device": "cpu",
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/api/v1/video/describe":
            body = json.dumps({"description": "老师正在写字"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/v1/video/describe/stream":
            body = "data: 老师正在\n\ndata: 写字\n\nevent: end\ndata: done\n\n".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        return


@pytest.fixture(name="qwen_server")
def fixture_qwen_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QwenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_qwen3vl_health_check_allows_lazy_model(qwen_server):
    client = Qwen3VLRealtimeClient(base_url=qwen_server)

    health = client.health_check()

    assert health.reachable is True
    assert health.loaded is False
    assert health.model == "test-model"
    assert health.device == "cpu"


def test_qwen3vl_describe_and_stream_accumulates_delta(qwen_server):
    client = Qwen3VLRealtimeClient(base_url=qwen_server)

    assert client.describe(base64_frames=["/9j/4AAQ"], prompt="描述画面") == "老师正在写字"

    events = list(client.stream_describe(base64_frames=["/9j/4AAQ"], prompt="描述画面"))

    assert events == [
        {"event": "delta", "delta": "老师正在"},
        {"event": "delta", "delta": "老师正在写字"},
        {"event": "done"},
    ]
