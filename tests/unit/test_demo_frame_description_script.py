import importlib.util
from pathlib import Path


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self._text = text
        self.status_code = status_code

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def read(self):
        return self._text.encode("utf-8")


class _FakeStream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, exc_type, exc, tb):
        return False


def _load_demo_module():
    script_path = Path("scripts/demo_frame_description.py")
    spec = importlib.util.spec_from_file_location("demo_frame_description", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_consume_stream_supports_stream_response_without_read_text(monkeypatch):
    module = _load_demo_module()

    body = '{"type":"status","stage":"sampling"}\n{"type":"complete","stage":"completed"}\n'

    def _fake_httpx_stream(*args, **kwargs):
        return _FakeStream(_FakeResponse(body, status_code=200))

    monkeypatch.setattr(module.httpx, "stream", _fake_httpx_stream)

    events, err = module.consume_stream("http://example.test", {"k": "v"})

    assert err == ""
    assert [event.get("type") for event in events] == ["status", "complete"]
