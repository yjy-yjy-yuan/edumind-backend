"""Whisper 运行时管理。"""

from app.services.whisper.runtime import (
    PRODUCT_WHISPER_MODELS,
    WHISPER_MODEL_HIGHLIGHTS,
    WhisperRuntimeManager,
    clear_whisper_device_cache,
    get_supported_whisper_models,
    get_whisper_device,
    get_whisper_model_catalog,
    get_whisper_runtime_status,
    iter_whisper_model_catalog,
    normalize_whisper_model_name,
    shutdown_whisper_runtime,
    start_whisper_background_preload,
    transcribe_audio_with_whisper,
    whisper_runtime,
)
from app.services.whisper.debug import get_whisper_debug_logger
