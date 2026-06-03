"""服务端视频帧抽取工具，供实时画面描述在 iOS 跨域采帧失败时兜底。"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import tempfile
from time import perf_counter
from urllib.parse import urlparse

from PIL import Image

from app.core.config import settings
from app.services.frame_desc.debug import get_frame_description_debug_logger

frame_desc_debug_logger = get_frame_description_debug_logger()


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
    frame_desc_debug_logger.debug(
        "frame source url parse | input_url=%s | scheme=%s | netloc=%s | hostname=%s",
        video_url,
        parsed.scheme,
        parsed.netloc,
        parsed.hostname,
    )
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FrameSourceExtractionError("服务端抽帧仅支持 http/https 视频地址")

    host = str(parsed.hostname or "").lower()
    allowed = _allowed_hosts()
    frame_desc_debug_logger.debug(
        "frame source host check | host=%s | allowed_set=%s | is_allowed=%s",
        host,
        sorted(allowed),
        host in allowed,
    )
    if not allowed or host not in allowed:
        raise FrameSourceExtractionError(f"视频源 host 未在抽帧白名单中：{host or 'unknown'}")

    return parsed.geturl()


def _convert_extracted_image_to_jpeg_bytes(image_path: str) -> bytes:
    """Normalize ffmpeg output to JPEG bytes for the vision pipeline."""
    if not os.path.exists(image_path) or os.path.getsize(image_path) <= 0:
        raise FrameSourceExtractionError("服务端抽帧结果为空")
    try:
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise FrameSourceExtractionError("服务端抽帧结果不是有效图片") from exc


def _candidate_timestamps(timestamp: float, *, max_attempts: int) -> list[float]:
    """Try the requested position first, then step backward near the video tail."""
    candidates: list[float] = []
    offsets = (0.0, -0.5, -1.0, -2.0, -5.0)
    for offset in offsets[: max(1, int(max_attempts or 1))]:
        candidate = max(0.0, float(timestamp or 0) + offset)
        if all(abs(candidate - existing) > 0.001 for existing in candidates):
            candidates.append(candidate)
        if len(candidates) >= max(1, int(max_attempts or 1)):
            break
    return candidates


def _build_ffmpeg_command(
    *,
    ffmpeg: str,
    timestamp: float,
    headers_arg: str,
    source_url: str,
    output_path: str,
) -> list[str]:
    return [
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
        f"{timestamp:.3f}",
        "-headers",
        headers_arg,
        "-i",
        source_url,
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-vcodec",
        "png",
        output_path,
    ]


def extract_frame_from_video_url(
    *,
    video_url: str,
    timestamp: float,
    trace_id: str = "",
    auth_token: str = "",
) -> str:
    """从远程视频 URL 抽取一帧并返回 base64 JPEG。

    该函数只在 FRAME_DESC_ALLOW_SERVER_FRAME_FETCH=true 时可用；URL host 必须在白名单内，
    用于解决 iOS file:// WebView 无法对无 CORS 的云端视频做 canvas 采帧的问题。

    Args:
        video_url: 视频流地址（如 https://xxx/api/videos/25/stream）
        timestamp: 抽帧时间戳（秒）
        trace_id: 用于日志追踪
        auth_token: 可选的 Bearer token，用于需要认证的视频流（如 Authorization: Bearer <token>）
    """

    safe_timestamp = max(0.0, float(timestamp or 0))

    frame_desc_debug_logger.debug(
        "frame source extractor | trace_id=%s | allow_server_fetch=%s | allowed_hosts=%s",
        trace_id,
        _as_bool(getattr(settings, "FRAME_DESC_ALLOW_SERVER_FRAME_FETCH", False)),
        sorted(_allowed_hosts()),
    )
    if not _as_bool(getattr(settings, "FRAME_DESC_ALLOW_SERVER_FRAME_FETCH", False)):
        raise FrameSourceExtractionError("服务端视频抽帧未启用")

    frame_desc_debug_logger.debug(
        "start extract frames from stream | trace_id=%s | url=%s | stream_url=%s | timestamp=%.3f",
        trace_id,
        video_url,
        video_url,
        safe_timestamp,
    )
    source_url = _ensure_allowed_source(video_url)
    frame_desc_debug_logger.debug(
        "frame source url validated | trace_id=%s | source_url=%s",
        trace_id,
        source_url,
    )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FrameSourceExtractionError("ffmpeg 不可用，无法服务端抽帧")

    timeout = max(
        0.5,
        float(getattr(settings, "FRAME_DESC_SERVER_FRAME_FETCH_TIMEOUT_SECONDS", 3.0) or 3.0),
    )
    max_attempts = max(1, int(getattr(settings, "FRAME_DESC_SERVER_FRAME_FETCH_MAX_ATTEMPTS", 2) or 2))
    started = perf_counter()

    # 构建请求头：Referer + 可选的 Authorization token
    headers_parts: list[str] = [f"Referer: {source_url}"]
    if auth_token and auth_token.strip():
        safe_token = str(auth_token).strip()
        headers_parts.append(f"Authorization: Bearer {safe_token}")
        frame_desc_debug_logger.debug(
            "auth token will be sent to frame source | trace_id=%s | token_prefix=%s***",
            trace_id,
            safe_token[:8] if len(safe_token) > 8 else safe_token,
        )
    headers_arg = "\r\n".join(headers_parts)

    last_error = "服务端抽帧失败"
    try:
        for attempt_index, attempt_timestamp in enumerate(
            _candidate_timestamps(safe_timestamp, max_attempts=max_attempts),
            start=1,
        ):
            fd, output_path = tempfile.mkstemp(prefix="edumind-frame-desc-", suffix=".png")
            os.close(fd)
            cmd = _build_ffmpeg_command(
                ffmpeg=ffmpeg,
                timestamp=attempt_timestamp,
                headers_arg=headers_arg,
                source_url=source_url,
                output_path=output_path,
            )
            frame_desc_debug_logger.debug(
                "ffmpeg command | trace_id=%s | attempt=%d | seek_timestamp=%.3f | cmd_preview=%s",
                trace_id,
                attempt_index,
                attempt_timestamp,
                " ".join(cmd[:8]) + " ... -headers [auth_headers] -i [url] -frames:v 1 -vcodec png ... [output]",
            )

            try:
                frame_desc_debug_logger.debug(
                    "server frame extract start | trace_id=%s | url=%s | stream_url=%s | timestamp=%.3f | seek_timestamp=%.3f | attempt=%d | timeout=%.1f",
                    trace_id,
                    source_url,
                    source_url,
                    safe_timestamp,
                    attempt_timestamp,
                    attempt_index,
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
                output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()[:500]
                    is_403 = "403" in detail or "forbidden" in detail.lower()
                    is_access_denied = "access denied" in detail.lower()
                    last_error = detail or "服务端抽帧失败"
                    frame_desc_debug_logger.debug(
                        "frame extract attempt failed | error_type=server_frame_extract_failed | trace_id=%s | elapsed_ms=%.2f | attempt=%d | seek_timestamp=%.3f | returncode=%s | output_bytes=%d | stream_url=%s | error=%s | contains_403_forbidden=%s | contains_access_denied=%s | server_frame_extract_failed",
                        trace_id,
                        elapsed_ms,
                        attempt_index,
                        attempt_timestamp,
                        result.returncode,
                        output_size,
                        source_url,
                        last_error,
                        is_403,
                        is_access_denied,
                        stack_info=True,
                    )
                    if is_403 or is_access_denied:
                        raise FrameSourceExtractionError(last_error)
                    continue

                try:
                    frame_bytes = _convert_extracted_image_to_jpeg_bytes(output_path)
                except FrameSourceExtractionError as exc:
                    last_error = str(exc)
                    frame_desc_debug_logger.debug(
                        "frame extract attempt produced invalid image | error_type=server_frame_extract_failed | trace_id=%s | elapsed_ms=%.2f | attempt=%d | seek_timestamp=%.3f | output_bytes=%d | stream_url=%s | error=%s | server_frame_extract_failed",
                        trace_id,
                        elapsed_ms,
                        attempt_index,
                        attempt_timestamp,
                        output_size,
                        source_url,
                        last_error,
                        exc_info=True,
                    )
                    continue

                if not frame_bytes:
                    last_error = "服务端抽帧结果为空"
                    continue

                frame_desc_debug_logger.debug(
                    "server frame extract done | trace_id=%s | elapsed_ms=%.2f | bytes=%d | attempt=%d | seek_timestamp=%.3f",
                    trace_id,
                    elapsed_ms,
                    len(frame_bytes),
                    attempt_index,
                    attempt_timestamp,
                )
                return base64.b64encode(frame_bytes).decode("ascii")
            finally:
                try:
                    os.remove(output_path)
                except OSError:
                    pass

        raise FrameSourceExtractionError(last_error)
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        frame_desc_debug_logger.debug(
            "frame extract failed | error_type=server_frame_extract_failed | trace_id=%s | elapsed_ms=%.2f | timeout=%.1f | stream_url=%s | error=timeout | server_frame_extract_failed",
            trace_id,
            elapsed_ms,
            timeout,
            source_url,
            exc_info=True,
        )
        raise FrameSourceExtractionError("服务端抽帧超时") from exc
