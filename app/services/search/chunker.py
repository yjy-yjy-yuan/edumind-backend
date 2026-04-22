"""视频切片模块 - 改编自 SentrySearch"""

import functools
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".mov")


def is_supported_video_file(path: str) -> bool:
    """检查是否为支持的视频文件格式"""
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS


def _ffmpeg_runs(path: str) -> bool:
    """检查 ffmpeg 是否可用"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            probe_path = tmp.name
        try:
            result = subprocess.run(
                [path, "-y", "-f", "lavfi", "-i", "nullsrc=s=2x2:d=0.1", "-frames:v", "1", probe_path],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0 and os.path.getsize(probe_path) > 0
        finally:
            os.unlink(probe_path)
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def _get_ffmpeg_executable() -> str:
    """获取可用的 ffmpeg 执行文件路径"""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg and _ffmpeg_runs(system_ffmpeg):
        return system_ffmpeg

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg not found. Install ffmpeg or imageio-ffmpeg.") from exc


def _parse_duration_from_ffmpeg_output(stderr_text: str) -> float:
    """从 ffmpeg stderr 解析时长"""
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text)
    if not match:
        for line in stderr_text.splitlines():
            lower = line.lower()
            if "no such file" in lower:
                raise FileNotFoundError(f"Video file not found: {line.strip()}")
            if "error" in lower:
                raise RuntimeError(f"ffmpeg error: {line.strip()}")
        raise RuntimeError("Could not determine video duration from ffmpeg output.")

    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）"""
    ffprobe_exe = shutil.which("ffprobe")
    if ffprobe_exe:
        try:
            result = subprocess.run(
                [
                    ffprobe_exe,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    video_path,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            info = json.loads(result.stdout)
            return float(info["format"]["duration"])
        except Exception as e:
            logger.warning(f"ffprobe failed: {e}, fallback to ffmpeg")

    ffmpeg_exe = _get_ffmpeg_executable()
    result = subprocess.run(
        [ffmpeg_exe, "-i", video_path],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return _parse_duration_from_ffmpeg_output(result.stderr)


def chunk_video(
    video_path: str,
    chunk_duration: int = 30,
    overlap: int = 5,
    preprocess: bool = False,
    target_resolution: int = 480,
    target_fps: int = 5,
) -> List[dict]:
    """
    将视频分割为重叠的片段

    Args:
        video_path: 视频文件路径
        chunk_duration: 切片时长（秒）
        overlap: 切片重叠（秒）
        preprocess: 是否预处理（降低分辨率/FPS）
        target_resolution: 目标分辨率高度
        target_fps: 目标 FPS

    Returns:
        片段列表，每个包含：chunk_path, source_file, start_time, end_time

    Raises:
        ValueError: 如果 chunk_duration <= 0 或 overlap < 0 或 overlap >= chunk_duration
        FileNotFoundError: 如果文件不存在
        RuntimeError: 如果 FFmpeg 执行失败
    """
    # 输入校验
    if chunk_duration <= 0:
        raise ValueError(f"chunk_duration must be positive, got {chunk_duration}")
    if overlap < 0:
        raise ValueError(f"overlap must be non-negative, got {overlap}")
    if overlap >= chunk_duration:
        raise ValueError(f"overlap ({overlap}) must be less than chunk_duration ({chunk_duration})")

    video_path = str(Path(video_path).resolve())
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    ffmpeg_exe = _get_ffmpeg_executable()
    duration = get_video_duration(video_path)
    tmp_dir = tempfile.mkdtemp(prefix="edumind_chunks_")
    step = chunk_duration - overlap
    chunks = []

    if duration <= chunk_duration:
        chunk_path = os.path.join(tmp_dir, "chunk_000.mp4")
        _create_chunk(ffmpeg_exe, video_path, chunk_path, 0, duration, preprocess, target_resolution, target_fps)
        return [
            {
                "chunk_path": chunk_path,
                "source_file": video_path,
                "start_time": 0.0,
                "end_time": duration,
            }
        ]

    start = 0.0
    idx = 0
    while start < duration:
        end = min(start + chunk_duration, duration)
        chunk_path = os.path.join(tmp_dir, f"chunk_{idx:03d}.mp4")

        _create_chunk(ffmpeg_exe, video_path, chunk_path, start, end, preprocess, target_resolution, target_fps)

        chunks.append(
            {
                "chunk_path": chunk_path,
                "source_file": video_path,
                "start_time": start,
                "end_time": end,
            }
        )
        start += step
        idx += 1

    logger.info(f"Created {len(chunks)} chunks from {video_path}")
    return chunks


def _create_chunk(
    ffmpeg_exe: str,
    video_path: str,
    chunk_path: str,
    start_time: float,
    end_time: float,
    preprocess: bool = False,
    target_resolution: int = 480,
    target_fps: int = 5,
) -> None:
    """创建单个视频片段

    Args:
        ffmpeg_exe: FFmpeg 可执行文件路径
        video_path: 源视频路径
        chunk_path: 输出片段路径
        start_time: 片段开始时间（秒）
        end_time: 片段结束时间（秒）
        preprocess: 是否预处理（降低分辨率/FPS）
        target_resolution: 目标分辨率高度
        target_fps: 目标 FPS

    Raises:
        RuntimeError: 如果 FFmpeg 执行失败
    """
    duration = end_time - start_time

    cmd = [
        ffmpeg_exe,
        "-y",
        "-ss",
        str(start_time),
        "-i",
        video_path,
        "-t",
        str(duration),
    ]

    if preprocess:
        # 降低分辨率和 FPS 以减少数据传输
        cmd.extend(
            [
                "-vf",
                f"scale=-1:{target_resolution},fps={target_fps}",
            ]
        )

    cmd.extend(
        [
            "-c:v",
            "libx264" if preprocess else "copy",
            "-c:a",
            "aac" if preprocess else "copy",
            chunk_path,
        ]
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg failed to create chunk {chunk_path}:\n"
                f"  Command: {' '.join(cmd)}\n"
                f"  Error: {result.stderr}"
            )
        logger.debug(f"Created chunk: {chunk_path} ({start_time:.1f}s-{end_time:.1f}s)")
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"FFmpeg timeout creating {chunk_path}: {e}") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"FFmpeg executable not found at {ffmpeg_exe}: {e}") from e
