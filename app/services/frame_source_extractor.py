"""服务端视频帧抽取工具，供实时画面描述在 iOS 跨域采帧失败时兜底。"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
from time import perf_counter
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)


class FrameSourceExtractionError(RuntimeError):
    """服务端视频帧抽取失败。"""


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _allowed_hosts() -> set[str]:
    raw = getattr(settings, "FRAME_DESC_SERVER_FRAME_ALLOWED_HOSTS", "")
    values = raw if isinstance(raw, list) else str(raw or "").split(",")
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _ensure_allowed_source(video_url: str) -> str:
    parsed = urlparse(str(video_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FrameSourceExtractionError("服务端抽帧仅支持 http/https 视频地址")

    host = str(parsed.hostname or "").lower()
    allowed = _allowed_hosts()
    if not allowed or host not in allowed:
        raise FrameSourceExtractionError(f"视频源 host 未在抽帧白名单中：{host or 'unknown'}")

    return parsed.geturl()


def extract_frame_from_video_url(
    *,
    video_url: str,
    timestamp: float,
    trace_id: str = "",
) -> str:
    """从远程视频 URL 抽取一帧并返回 base64 JPEG。

    该函数只在 FRAME_DESC_ALLOW_SERVER_FRAME_FETCH=true 时可用；URL host 必须在白名单内，
    用于解决 iOS file:// WebView 无法对无 CORS 的云端视频做 canvas 采帧的问题。
    """

    if not _as_bool(getattr(settings, "FRAME_DESC_ALLOW_SERVER_FRAME_FETCH", False)):
        raise FrameSourceExtractionError("服务端视频抽帧未启用")

    source_url = _ensure_allowed_source(video_url)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FrameSourceExtractionError("ffmpeg 不可用，无法服务端抽帧")

    timeout = max(
        5.0,
        float(getattr(settings, "FRAME_DESC_SERVER_FRAME_FETCH_TIMEOUT_SECONDS", 35.0) or 35.0),
    )
    safe_timestamp = max(0.0, float(timestamp or 0))
    started = perf_counter()
    fd, output_path = tempfile.mkstemp(prefix="edumind-frame-desc-", suffix=".jpg")
    os.close(fd)

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-ss",
        f"{safe_timestamp:.3f}",
        "-i",
        source_url,
        "-frames:v",
        "1",
        "-q:v",
        "5",
        output_path,
    ]

    try:
        logger.debug(
            "[frame_desc_debug] server frame extract start | trace=%s | url=%s | timestamp=%.3f | timeout=%.1f",
            trace_id,
            source_url,
            safe_timestamp,
            timeout,
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed_ms = (perf_counter() - started) * 1000
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            logger.warning(
                "[frame_desc_debug] server frame extract failed | trace=%s | elapsed_ms=%.2f | returncode=%s | detail=%s",
                trace_id,
                elapsed_ms,
                result.returncode,
                detail,
            )
            raise FrameSourceExtractionError(detail or "服务端抽帧失败")

        with open(output_path, "rb") as fh:
            frame_bytes = fh.read()
        if not frame_bytes:
            raise FrameSourceExtractionError("服务端抽帧结果为空")

        logger.debug(
            "[frame_desc_debug] server frame extract done | trace=%s | elapsed_ms=%.2f | bytes=%d",
            trace_id,
            elapsed_ms,
            len(frame_bytes),
        )
        return base64.b64encode(frame_bytes).decode("ascii")
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        logger.warning(
            "[frame_desc_debug] server frame extract timeout | trace=%s | elapsed_ms=%.2f | timeout=%.1f",
            trace_id,
            elapsed_ms,
            timeout,
        )
        raise FrameSourceExtractionError("服务端抽帧超时") from exc
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass
