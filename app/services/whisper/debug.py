"""Dedicated debug logger for Whisper runtime diagnostics."""

from __future__ import annotations

import logging
from logging import FileHandler
from pathlib import Path

from app.core.config import settings

LOGGER_NAME = "whisper_debug"
_HANDLER_NAME = "whisper_debug_file"
_DEFAULT_LOG_FILE = "logs/whisper_debug.log"


def get_whisper_debug_logger() -> logging.Logger:
    """Return the dedicated Whisper debug logger with a file handler."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    raw_path = str(getattr(settings, "WHISPER_DEBUG_LOG_FILE", _DEFAULT_LOG_FILE) or _DEFAULT_LOG_FILE).strip()
    log_path = Path(raw_path)
    if not log_path.is_absolute():
        log_path = Path(settings.BASE_DIR) / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    target = str(log_path)

    exists = any(
        getattr(handler, "name", "") == _HANDLER_NAME and getattr(handler, "baseFilename", "") == target
        for handler in logger.handlers
    )
    if not exists:
        file_handler = FileHandler(target, mode="a", encoding="utf-8")
        file_handler.name = _HANDLER_NAME
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        logger.addHandler(file_handler)

    return logger
