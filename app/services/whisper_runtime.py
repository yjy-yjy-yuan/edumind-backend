"""Whisper 运行时管理。

负责统一处理模型设备选择、模型目录、启动预热、加载超时与单模型复用。
"""

import gc
import hashlib
import inspect
import logging
import os
import threading
import time
from copy import deepcopy
from typing import Any
from typing import Iterable
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

PRODUCT_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large", "large-v3", "large-v3-turbo", "turbo")
WHISPER_MODEL_HIGHLIGHTS = {
    "tiny": "最快，适合先验证上传和转录流程是否跑通。",
    "base": "默认最稳，资源占用较低，适合日常使用。",
    "small": "速度和准确率更平衡，适合作为常用升级档。",
    "medium": "准确率更高，适合正式内容转录。",
    "large": "效果最好，适合高质量要求场景。",
    "large-v3": "中文、方言和复杂口音效果更稳，适合严谨转录。",
    "large-v3-turbo": "接近 large-v3 的效果，但推理更快，适合高质量与速度兼顾。",
    "turbo": "优先提速，适合想更快拿到初稿。",
}


class WhisperRuntimeManager:
    """Whisper 模型运行时管理器。

    当前实现只保留一个已加载模型，避免不同模型长期常驻造成内存占用失控。
    """

    def __init__(self):
        self._load_lock = threading.RLock()
        self._transcribe_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._loaded_model: Any | None = None
        self._loaded_key: tuple[str, str, str] | None = None
        self._preload_thread: threading.Thread | None = None
        self._status = {
            "state": "idle",
            "enabled": bool(getattr(settings, "WHISPER_PRELOAD_ON_STARTUP", False)),
            "loaded": False,
            "model": "",
            "device": "",
            "model_path": os.path.expanduser(settings.WHISPER_MODEL_PATH),
            "downloaded": False,
            "cache_hit": False,
            "last_source": "",
            "last_error": "",
            "timeout_seconds": 0,
            "loaded_at": None,
        }

    def get_device(self) -> str:
        """获取当前最合适的 Whisper 设备。"""
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"

            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                pytorch_version = torch.__version__.split("+")[0]
                major, minor = map(int, pytorch_version.split(".")[:2])
                if major > 2 or (major == 2 and minor >= 1):
                    return "mps"
                logger.warning("PyTorch %s 的 MPS 兼容性不足，回退到 CPU", pytorch_version)
        except ImportError:
            logger.warning("未安装 torch，Whisper 将回退到 CPU")
        except Exception as exc:
            logger.warning("检测 Whisper 设备失败，回退到 CPU | error=%s", exc)

        return "cpu"

    def clear_device_cache(self):
        """清理 CUDA/MPS 缓存。"""
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("已清理 CUDA 缓存")
            elif hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                torch.mps.empty_cache()
                logger.info("已清理 MPS 缓存")
        except Exception as exc:
            logger.warning("清理 Whisper 设备缓存失败 | error=%s", exc)

        gc.collect()

    def should_retry_on_cpu(self, device: str, error: Exception) -> bool:
        """判断是否应从 MPS 自动回退到 CPU。"""
        if device != "mps":
            return False

        message = str(error)
        fallback_markers = (
            "SparseMPS",
            "sparse_coo_tensor",
            "not implemented for the 'MPS' backend",
            "Could not run",
        )
        return any(marker in message for marker in fallback_markers)

    def get_status(self) -> dict:
        """返回当前运行时状态。"""
        with self._status_lock:
            return deepcopy(self._status)

    def _update_status(self, **changes):
        with self._status_lock:
            self._status.update(changes)

    def _resolve_model_name(self, model_name: str) -> str:
        normalized = str(model_name or settings.WHISPER_MODEL).strip()
        return normalized or settings.WHISPER_MODEL

    def list_supported_models(self) -> list[str]:
        """返回当前运行时实际支持的产品模型列表。"""
        try:
            import whisper

            available = {str(name).strip() for name in getattr(whisper, "_MODELS", {}).keys() if str(name).strip()}
        except Exception:
            logger.warning("读取 Whisper 模型目录失败，回退到内置产品模型列表")
            available = set(PRODUCT_WHISPER_MODELS)

        preferred = [name for name in PRODUCT_WHISPER_MODELS if name in available]
        if preferred:
            return preferred

        fallback = [name for name in available if name]
        return sorted(fallback) if fallback else list(PRODUCT_WHISPER_MODELS)

    def is_supported_model(self, model_name: str) -> bool:
        normalized_name = self._resolve_model_name(model_name).lower()
        return normalized_name in set(self.list_supported_models())

    def normalize_supported_model_name(self, model_name: str) -> str:
        normalized_name = self._resolve_model_name(model_name).strip().lower()
        supported_models = self.list_supported_models()
        if normalized_name in supported_models:
            return normalized_name

        default_model = str(settings.WHISPER_MODEL or "").strip().lower()
        if not str(model_name or "").strip() and default_model in supported_models:
            return default_model

        raise ValueError(
            f"不支持的 Whisper 模型: {normalized_name or model_name}，当前仅支持: {', '.join(supported_models)}"
        )

    def _resolve_model_path(self, model_path: str = "", *, create_dir: bool = False) -> str:
        expanded_path = os.path.expanduser(model_path or settings.WHISPER_MODEL_PATH)
        if create_dir:
            os.makedirs(expanded_path, exist_ok=True)
        return expanded_path

    def _resolve_model_filename(self, model_name: str) -> str:
        normalized_name = self._resolve_model_name(model_name)

        try:
            import whisper

            model_urls = getattr(whisper, "_MODELS", {})
            model_url = model_urls.get(normalized_name)
            if model_url:
                return os.path.basename(urlparse(model_url).path)
        except Exception:
            logger.debug("无法通过 whisper._MODELS 解析模型文件名 | model=%s", normalized_name)

        return f"{normalized_name}.pt"

    def _resolve_model_url(self, model_name: str) -> str:
        normalized_name = self._resolve_model_name(model_name)
        try:
            import whisper

            return str(getattr(whisper, "_MODELS", {}).get(normalized_name, "") or "")
        except Exception:
            return ""

    def _resolve_expected_sha256(self, model_name: str) -> str:
        model_url = self._resolve_model_url(model_name)
        if not model_url:
            return ""

        path_parts = [part for part in urlparse(model_url).path.split("/") if part]
        if len(path_parts) < 2:
            return ""

        candidate = path_parts[-2].strip().lower()
        if len(candidate) == 64 and all(ch in "0123456789abcdef" for ch in candidate):
            return candidate
        return ""

    def _locate_downloaded_model_file(self, model_name: str, model_path: str = "") -> str:
        resolved_path = self._resolve_model_path(model_path)
        filename = self._resolve_model_filename(model_name)
        candidate_paths = (
            os.path.join(resolved_path, filename),
            os.path.join(os.path.expanduser("~/.cache/whisper"), filename),
        )
        for path in candidate_paths:
            if os.path.exists(path):
                return path
        return ""

    def _compute_file_sha256(self, file_path: str) -> str:
        digest = hashlib.sha256()
        with open(file_path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def diagnose_model_file_integrity(self, model_name: str, model_path: str = "") -> str:
        file_path = self._locate_downloaded_model_file(model_name, model_path)
        expected_sha256 = self._resolve_expected_sha256(model_name)

        if not file_path or not expected_sha256:
            return ""

        try:
            actual_sha256 = self._compute_file_sha256(file_path)
        except Exception as exc:
            return f"无法校验本地 Whisper 模型文件完整性：{exc}"

        if actual_sha256 == expected_sha256:
            return ""

        return (
            f"本地 Whisper 模型文件校验失败 | model={self._resolve_model_name(model_name)} | "
            f"file={file_path} | expected_sha256={expected_sha256} | actual_sha256={actual_sha256}。"
            "请删除该模型文件后重新下载。"
        )

    def is_model_downloaded(self, model_name: str, model_path: str = "") -> bool:
        """检查模型文件是否已存在于自定义目录或默认缓存目录。"""
        resolved_path = self._resolve_model_path(model_path)
        filename = self._resolve_model_filename(model_name)
        candidate_paths = (
            os.path.join(resolved_path, filename),
            os.path.join(os.path.expanduser("~/.cache/whisper"), filename),
        )
        return any(os.path.exists(path) for path in candidate_paths)

    def get_load_timeout_seconds(self, model_name: str, model_path: str = "") -> int:
        """根据模型文件是否已存在选择加载超时。"""
        downloaded = self.is_model_downloaded(model_name, model_path)
        if downloaded:
            return max(1, int(settings.WHISPER_LOAD_TIMEOUT_SECONDS))
        return max(1, int(settings.WHISPER_DOWNLOAD_TIMEOUT_SECONDS))

    def _load_model_with_timeout(self, model_name: str, device: str, model_path: str, timeout_seconds: int):
        result_holder: dict[str, Any] = {}
        error_holder: dict[str, BaseException] = {}

        def worker():
            try:
                import whisper

                result_holder["model"] = whisper.load_model(model_name, device=device, download_root=model_path)
            except BaseException as exc:  # pragma: no cover - 线程异常保护
                error_holder["error"] = exc

        thread = threading.Thread(
            target=worker,
            name=f"whisper-load-{model_name}-{device}",
            daemon=True,
        )
        thread.start()
        thread.join(timeout_seconds)

        if thread.is_alive():
            raise TimeoutError(f"加载 {model_name} 模型超时 (超过{timeout_seconds}秒)")

        if "error" in error_holder:
            raise error_holder["error"]

        return result_holder.get("model")

    def unload_model(self):
        """释放当前已加载模型。"""
        with self._load_lock:
            if self._loaded_model is None:
                return

            model_name = self._loaded_key[0] if self._loaded_key else ""
            self._loaded_model = None
            self._loaded_key = None
            self.clear_device_cache()
            self._update_status(
                state="idle",
                loaded=False,
                model="",
                device="",
                cache_hit=False,
                timeout_seconds=0,
            )
            logger.info("已释放 Whisper 模型 | model=%s", model_name)

    def load_model(
        self,
        model_name: str,
        *,
        force_device: str = "",
        model_path: str = "",
        source: str = "task",
    ):
        """加载指定 Whisper 模型，并保留为当前活动模型。"""
        normalized_name = self._resolve_model_name(model_name)
        resolved_path = self._resolve_model_path(model_path, create_dir=True)
        device = force_device or self.get_device()
        cache_key = (normalized_name, device, resolved_path)

        with self._load_lock:
            if self._loaded_model is not None and self._loaded_key == cache_key:
                self._update_status(
                    state="ready",
                    enabled=bool(settings.WHISPER_PRELOAD_ON_STARTUP),
                    loaded=True,
                    model=normalized_name,
                    device=device,
                    model_path=resolved_path,
                    downloaded=self.is_model_downloaded(normalized_name, resolved_path),
                    cache_hit=True,
                    last_source=source,
                    last_error="",
                )
                return self._loaded_model

            if self._loaded_model is not None and self._loaded_key != cache_key:
                previous_model = self._loaded_key[0] if self._loaded_key else ""
                self._loaded_model = None
                self._loaded_key = None
                self.clear_device_cache()
                logger.info("切换 Whisper 模型前释放旧模型 | from=%s | to=%s", previous_model, normalized_name)

            timeout_seconds = self.get_load_timeout_seconds(normalized_name, resolved_path)
            downloaded = self.is_model_downloaded(normalized_name, resolved_path)
            self._update_status(
                state="loading",
                enabled=bool(settings.WHISPER_PRELOAD_ON_STARTUP),
                loaded=False,
                model=normalized_name,
                device=device,
                model_path=resolved_path,
                downloaded=downloaded,
                cache_hit=False,
                last_source=source,
                last_error="",
                timeout_seconds=timeout_seconds,
            )

            started_at = time.time()
            logger.info(
                "开始加载 Whisper 模型 | model=%s | device=%s | path=%s | downloaded=%s | timeout=%ss | source=%s",
                normalized_name,
                device,
                resolved_path,
                downloaded,
                timeout_seconds,
                source,
            )

            try:
                model = self._load_model_with_timeout(normalized_name, device, resolved_path, timeout_seconds)
            except Exception as exc:
                integrity_error = self.diagnose_model_file_integrity(normalized_name, resolved_path)
                error_message = integrity_error or str(exc)
                self._update_status(
                    state="failed",
                    loaded=False,
                    cache_hit=False,
                    last_error=error_message,
                    timeout_seconds=timeout_seconds,
                )
                if integrity_error:
                    raise RuntimeError(integrity_error) from exc
                raise

            elapsed = time.time() - started_at
            self._loaded_model = model
            self._loaded_key = cache_key
            self._update_status(
                state="ready",
                loaded=True,
                model=normalized_name,
                device=device,
                model_path=resolved_path,
                downloaded=True,
                cache_hit=False,
                last_source=source,
                last_error="",
                timeout_seconds=timeout_seconds,
                loaded_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            )
            logger.info(
                "Whisper 模型加载完成 | model=%s | device=%s | elapsed=%.1fs | source=%s",
                normalized_name,
                device,
                elapsed,
                source,
            )
            return model

    def transcribe(
        self,
        audio_path: str,
        model_name: str,
        language: str,
        model_path: str = "",
        *,
        force_device: str = "",
    ) -> dict:
        """使用当前运行时管理器执行转录。"""
        device = force_device or self.get_device()
        normalized_language = str(language or "").strip().lower()
        normalized_model = self._resolve_model_name(model_name).strip().lower()
        transcribe_options: dict[str, Any] = {
            "language": language if language else None,
            "verbose": False,
            "fp16": (device in {"cuda", "mps"}),
        }

        if normalized_language.startswith("zh"):
            transcribe_options.update(
                {
                    "temperature": (0.0, 0.2),
                    "condition_on_previous_text": True,
                    "initial_prompt": (
                        "以下是中文教学视频逐字转录，可能包含普通话、儿化音、粤语词汇、吴语口音或课堂术语。"
                        "请尽量按原话准确转写，不要总结，不要意译。"
                    ),
                }
            )
        if normalized_model in {"large", "large-v3", "large-v3-turbo"}:
            transcribe_options["hallucination_silence_threshold"] = 1.2

        try:
            with self._transcribe_lock:
                model = self.load_model(
                    model_name,
                    force_device=device,
                    model_path=model_path,
                    source="transcribe",
                )
                start_time = time.time()
                filtered_options = self._filter_transcribe_options(model.transcribe, transcribe_options)
                result = model.transcribe(audio_path, **filtered_options)

            elapsed = time.time() - start_time
            logger.info(
                "Whisper 转录完成 | model=%s | device=%s | file=%s | elapsed=%.1fs",
                self._resolve_model_name(model_name),
                device,
                os.path.basename(audio_path),
                elapsed,
            )
            self.clear_device_cache()
            return result
        except Exception as exc:
            logger.error(
                "Whisper 转录失败 | model=%s | device=%s | file=%s | error=%s",
                self._resolve_model_name(model_name),
                device,
                os.path.basename(audio_path),
                exc,
            )
            self.clear_device_cache()
            if self.should_retry_on_cpu(device, exc):
                logger.warning("MPS 转录失败，自动切换到 CPU 重试 | model=%s", model_name)
                return self.transcribe(
                    audio_path,
                    model_name,
                    language,
                    model_path,
                    force_device="cpu",
                )
            raise

    def _filter_transcribe_options(self, transcribe_callable: Any, options: dict[str, Any]) -> dict[str, Any]:
        """Filter optional kwargs for Whisper implementations with narrower signatures.

        Some test doubles and older wrappers only accept a subset of OpenAI Whisper's
        keyword arguments. We keep the core options and silently drop unsupported extras.
        """
        try:
            signature = inspect.signature(transcribe_callable)
        except (TypeError, ValueError):
            return options

        parameters = signature.parameters.values()
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return options

        accepted = {parameter.name for parameter in parameters}
        filtered = {key: value for key, value in options.items() if key in accepted}
        dropped = sorted(set(options) - set(filtered))
        if dropped:
            logger.debug("Whisper transcribe() 不支持部分可选参数，已自动忽略 | dropped=%s", ",".join(dropped))
        return filtered

    def start_background_preload(self, model_name: str = "", model_path: str = "") -> bool:
        """在后台线程预热默认 Whisper 模型。"""
        target_model = self._resolve_model_name(model_name)
        if not bool(settings.WHISPER_PRELOAD_ON_STARTUP):
            resolved_path = self._resolve_model_path(model_path)
            self._update_status(
                state="disabled",
                enabled=False,
                model=target_model,
                model_path=resolved_path,
                last_source="startup_preload",
            )
            logger.info("Whisper 启动预热已禁用 | model=%s", target_model)
            return False

        resolved_path = self._resolve_model_path(model_path)

        with self._load_lock:
            if self._preload_thread is not None and self._preload_thread.is_alive():
                logger.info("Whisper 启动预热已在进行中，跳过重复启动")
                return False

            self._update_status(
                state="queued",
                enabled=True,
                model=target_model,
                model_path=resolved_path,
                cache_hit=False,
                last_source="startup_preload",
                last_error="",
            )

            def worker():
                try:
                    self.load_model(
                        target_model,
                        model_path=resolved_path,
                        source="startup_preload",
                    )
                except Exception as exc:
                    logger.warning("Whisper 启动预热失败 | model=%s | error=%s", target_model, exc)

            self._preload_thread = threading.Thread(
                target=worker,
                name="whisper-startup-preload",
                daemon=True,
            )
            self._preload_thread.start()

        logger.info("Whisper 启动预热已开始 | model=%s | path=%s", target_model, resolved_path)
        return True

    def shutdown(self):
        """应用关闭时释放 Whisper 运行时资源。"""
        self.unload_model()


whisper_runtime = WhisperRuntimeManager()


def get_whisper_device() -> str:
    return whisper_runtime.get_device()


def clear_whisper_device_cache():
    whisper_runtime.clear_device_cache()


def get_supported_whisper_models() -> list[str]:
    return whisper_runtime.list_supported_models()


def normalize_whisper_model_name(model_name: str) -> str:
    return whisper_runtime.normalize_supported_model_name(model_name)


def iter_whisper_model_catalog(model_path: str = "") -> Iterable[dict]:
    resolved_path = whisper_runtime._resolve_model_path(model_path)
    supported_models = set(get_supported_whisper_models())

    for model_name in PRODUCT_WHISPER_MODELS:
        if model_name not in supported_models:
            continue
        yield {
            "value": model_name,
            "label": model_name,
            "highlight": WHISPER_MODEL_HIGHLIGHTS.get(model_name, ""),
            "downloaded": whisper_runtime.is_model_downloaded(model_name, resolved_path),
        }


def get_whisper_model_catalog(model_path: str = "") -> list[dict]:
    return list(iter_whisper_model_catalog(model_path=model_path))


def transcribe_audio_with_whisper(
    audio_path: str,
    model_name: str,
    language: str,
    model_path: str = "",
    *,
    force_device: str = "",
) -> dict:
    return whisper_runtime.transcribe(
        audio_path,
        model_name,
        language,
        model_path,
        force_device=force_device,
    )


def get_whisper_runtime_status() -> dict:
    return whisper_runtime.get_status()


def start_whisper_background_preload(model_name: str = "", model_path: str = "") -> bool:
    return whisper_runtime.start_background_preload(model_name=model_name, model_path=model_path)


def shutdown_whisper_runtime():
    whisper_runtime.shutdown()
