"""Subtitle IO helpers with charset fallback and robust SRT conversion."""

from __future__ import annotations

import re
from pathlib import Path

SRT_TIME_LINE_RE = re.compile(r"^\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})(.*)$")

# Ordered by probability in this project/runtime.
SUBTITLE_DECODE_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
    "big5",
)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_subtitle_file_with_fallback(path: str) -> str:
    """Read subtitle file text using common Chinese-friendly charset fallback."""
    file_path = Path(path)
    raw = file_path.read_bytes()

    last_error = None
    for enc in SUBTITLE_DECODE_ENCODINGS:
        try:
            return _normalize_newlines(raw.decode(enc))
        except UnicodeDecodeError as exc:
            last_error = exc

    # Final fallback: replace undecodable bytes to avoid hard-fail for runtime playback.
    if last_error is not None:
        return _normalize_newlines(raw.decode("utf-8", errors="replace"))
    return _normalize_newlines(raw.decode("utf-8"))


def srt_to_vtt(srt_content: str) -> str:
    """Convert SRT text to VTT text by modifying only timing lines."""
    lines = _normalize_newlines(srt_content).split("\n")
    out_lines = ["WEBVTT", ""]
    for line in lines:
        match = SRT_TIME_LINE_RE.match(line)
        if not match:
            out_lines.append(line)
            continue
        start_hms, start_ms, end_hms, end_ms, tail = match.groups()
        out_lines.append(f"{start_hms}.{start_ms} --> {end_hms}.{end_ms}{tail}")
    return "\n".join(out_lines).strip() + "\n"


def srt_to_plain_text(srt_content: str) -> str:
    """Drop SRT index/timing rows and keep subtitle body."""
    lines = _normalize_newlines(srt_content).split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if stripped.isdigit():
            continue
        if SRT_TIME_LINE_RE.match(stripped):
            continue
        out.append(line)

    # Collapse excessive blank lines.
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + ("\n" if text else "")
