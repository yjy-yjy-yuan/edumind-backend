"""Whisper 运行时单元测试。"""

import sys
import time
import types

import pytest
from app.core.config import settings
from app.services.whisper_runtime import WhisperRuntimeManager


@pytest.mark.unit
def test_load_model_reuses_cached_instance(tmp_path, monkeypatch):
    """同一模型重复加载时应复用当前已加载实例。"""
    calls = []

    def fake_load_model(model_name, *, device, download_root):
        calls.append((model_name, device, download_root))
        return {"model_name": model_name, "device": device}

    fake_whisper = types.SimpleNamespace(
        _MODELS={"small": "https://example.com/models/small.pt"},
        load_model=fake_load_model,
    )
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    manager = WhisperRuntimeManager()
    model_path = str(tmp_path)

    first = manager.load_model("small", force_device="cpu", model_path=model_path, source="test")
    second = manager.load_model("small", force_device="cpu", model_path=model_path, source="test")

    assert first is second
    assert calls == [("small", "cpu", model_path)]
    assert manager.get_status()["cache_hit"] is True


@pytest.mark.unit
def test_load_timeout_switches_between_download_and_local_cache(tmp_path, monkeypatch):
    """模型文件已存在时应使用较短超时，不存在时使用下载超时。"""
    fake_whisper = types.SimpleNamespace(
        _MODELS={"base": "https://example.com/models/base.pt"},
        load_model=lambda *args, **kwargs: object(),
    )
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    monkeypatch.setattr(settings, "WHISPER_LOAD_TIMEOUT_SECONDS", 61)
    monkeypatch.setattr(settings, "WHISPER_DOWNLOAD_TIMEOUT_SECONDS", 301)

    manager = WhisperRuntimeManager()

    assert manager.get_load_timeout_seconds("base", str(tmp_path)) == 301

    (tmp_path / "base.pt").write_bytes(b"ready")
    assert manager.get_load_timeout_seconds("base", str(tmp_path)) == 61


@pytest.mark.unit
def test_transcribe_retries_on_cpu_after_mps_failure(tmp_path, monkeypatch):
    """MPS 不兼容错误应自动回退到 CPU 重试。"""
    transcribe_calls = []

    class FakeModel:
        def __init__(self, device: str):
            self.device = device

        def transcribe(self, audio_path, *, language, verbose, fp16, **kwargs):
            # 生产代码对中文等场景会传入 temperature、initial_prompt 等；与 openai-whisper 一致接受额外参数
            transcribe_calls.append((self.device, audio_path, language, verbose, fp16))
            if self.device == "mps":
                raise RuntimeError("SparseMPS kernel missing")
            return {"text": "ok", "segments": []}

    manager = WhisperRuntimeManager()
    monkeypatch.setattr(manager, "get_device", lambda: "mps")
    monkeypatch.setattr(manager, "clear_device_cache", lambda: None)
    monkeypatch.setattr(
        manager,
        "load_model",
        lambda model_name, *, force_device="", model_path="", source="task": FakeModel(force_device or "cpu"),
    )

    result = manager.transcribe(str(tmp_path / "audio.wav"), "small", "zh", str(tmp_path))

    assert result["text"] == "ok"
    assert transcribe_calls == [
        ("mps", str(tmp_path / "audio.wav"), "zh", False, True),
        ("cpu", str(tmp_path / "audio.wav"), "zh", False, False),
    ]


@pytest.mark.unit
def test_load_model_surfaces_corrupted_local_model_file(tmp_path, monkeypatch):
    """本地模型文件校验失败时应返回明确错误，而不是泛化为下载/超时异常。"""

    def fake_load_model(*args, **kwargs):
        raise RuntimeError("download failed")

    expected_sha256 = "a" * 64
    fake_whisper = types.SimpleNamespace(
        _MODELS={
            "large": f"https://example.com/models/{expected_sha256}/large-v3.pt",
        },
        load_model=fake_load_model,
    )
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    model_file = tmp_path / "large-v3.pt"
    model_file.write_bytes(b"broken-model")

    manager = WhisperRuntimeManager()

    with pytest.raises(RuntimeError, match="本地 Whisper 模型文件校验失败"):
        manager.load_model("large", force_device="cpu", model_path=str(tmp_path), source="test")

    status = manager.get_status()
    assert status["state"] == "failed"
    assert status["last_error"].startswith("本地 Whisper 模型文件校验失败")
    assert str(model_file) in status["last_error"]


@pytest.mark.unit
def test_start_background_preload_updates_runtime_status(tmp_path, monkeypatch):
    """启动预热后应在后台完成模型加载并更新状态。"""
    calls = []

    def fake_load_model(model_name, *, device, download_root):
        calls.append((model_name, device, download_root))
        return {"model_name": model_name, "device": device}

    fake_whisper = types.SimpleNamespace(
        _MODELS={"base": "https://example.com/models/base.pt"},
        load_model=fake_load_model,
    )
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    monkeypatch.setattr(settings, "WHISPER_PRELOAD_ON_STARTUP", True)

    manager = WhisperRuntimeManager()
    monkeypatch.setattr(manager, "get_device", lambda: "cpu")

    started = manager.start_background_preload("base", str(tmp_path))

    assert started is True

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if manager.get_status()["state"] == "ready":
            break
        time.sleep(0.01)

    status = manager.get_status()
    assert status["state"] == "ready"
    assert status["last_source"] == "startup_preload"
    assert calls == [("base", "cpu", str(tmp_path))]
