"""Subtitle IO helpers with charset fallback and robust SRT conversion."""

from __future__ import annotations

import re
from pathlib import Path

SRT_TIME_LINE_RE = re.compile(r"^\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})(.*)$")
SRT_TIME_MARKER_RE = re.compile(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}")
_MOJIBAKE_MARKERS = (
    "Ã",
    "Â",
    "\ufffd",
    "ä¸",
    "æ",
    "å­",
    "å¹",
    "鑰佸",
    "璁茶",
    "涓",
    "瀛楀",
    "骞宠",
    "屽洓",
    "杈瑰",
    "瀵兼",
    "暟",
    "瀹氫",
)

_COMMON_GBK_MOJIBAKE_REPLACEMENTS = (
    ("骞宠屽洓杈瑰舰", "平行四边形"),
    ("骞宠屽洓杈瑰", "平行四边"),
    ("骞宠屽", "平行"),
    ("涓枃瀛楀箷", "中文字幕"),
    ("涓枃", "中文"),
    ("瀛楀箷", "字幕"),
    ("鑰佸笀", "老师"),
    ("璁茶В", "讲解"),
    ("瀵兼暟", "导数"),
    ("瀹氫箟", "定义"),
)

# Ordered by probability in this project/runtime.
SUBTITLE_DECODE_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "gbk",
    "big5",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _subtitle_text_score(text: str) -> float:
    """Score decoded subtitle text; higher means less likely to be mojibake."""
    if not text:
        return -10_000.0

    sample = text[:4000]
    length = max(1, len(sample))
    cjk = sum(1 for char in sample if "\u4e00" <= char <= "\u9fff")
    printable = sum(1 for char in sample if char.isprintable() or char in "\n\t")
    controls = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    replacements = sample.count("\ufffd")
    mojibake = sum(sample.count(marker) for marker in _MOJIBAKE_MARKERS)
    time_markers = len(SRT_TIME_MARKER_RE.findall(sample))

    return (
        printable / length * 20
        + min(cjk, 200) * 1.5
        + min(time_markers, 50) * 20
        - controls * 30
        - replacements * 80
        - mojibake * 4
    )


def repair_mojibake_text(text: str) -> str:
    """Repair common UTF-8-as-Latin1/GBK mojibake when doing so clearly improves the text."""
    normalized = _normalize_newlines(str(text or ""))
    if "\ufffd" not in normalized and not any(marker in normalized for marker in _MOJIBAKE_MARKERS):
        return normalized
    replaced = normalized
    for source, target in _COMMON_GBK_MOJIBAKE_REPLACEMENTS:
        replaced = replaced.replace(source, target)

    candidates = [normalized, replaced]
    for source_encoding in ("latin1", "cp1252", "gb18030", "gbk"):
        try:
            candidates.append(normalized.encode(source_encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        try:
            candidates.append(normalized.encode(source_encoding, errors="replace").decode("utf-8", errors="replace"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return max(candidates, key=_subtitle_text_score)


def read_subtitle_file_with_fallback(path: str) -> str:
    """Read subtitle file text using common Chinese-friendly charset fallback."""
    file_path = Path(path)
    raw = file_path.read_bytes()

    for enc in ("utf-8-sig", "utf-8"):
        try:
            decoded = repair_mojibake_text(raw.decode(enc))
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in decoded and not any(ord(char) < 32 and char not in "\n\r\t" for char in decoded):
            return decoded

    decoded_candidates: list[str] = []
    for enc in SUBTITLE_DECODE_ENCODINGS[2:]:
        try:
            decoded_candidates.append(repair_mojibake_text(raw.decode(enc)))
        except UnicodeDecodeError:
            continue

    decoded_candidates.append(repair_mojibake_text(raw.decode("utf-8", errors="replace")))
    return max(decoded_candidates, key=_subtitle_text_score)


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
