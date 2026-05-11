"""
视频处理后台任务 - FastAPI 版本

主要功能：
1. 视频预览图生成
2. 视频信息提取
3. 字幕生成和格式化
4. 音频转录 (支持 CUDA/MPS/CPU)
"""

import gc
import json
import logging
import os
import re
import subprocess
import threading
import time

from sqlalchemy import inspect

from app.services.video_content_service import extract_transcript_text
from app.services.whisper_runtime import (
    clear_whisper_device_cache,
    get_whisper_device,
    transcribe_audio_with_whisper,
)
from app.utils.subtitle_io import repair_mojibake_text

logger = logging.getLogger(__name__)

TRANSCRIPTION_PROGRESS_START = 60.0
TRANSCRIPTION_PROGRESS_END = 84.0
TRANSCRIPTION_POLL_SECONDS = 2.0


def build_safe_filename_stem(name: str, *, fallback: str = "video", max_length: int = 80) -> str:
    candidate = str(name or "").strip()
    candidate = re.sub(r'[<>:"/\\|?*]+', "_", candidate)
    candidate = re.sub(r"\s+", " ", candidate)
    candidate = candidate.strip(" ._-")
    if not candidate:
        candidate = fallback
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip(" ._-")
    return candidate or fallback


def rename_local_video_file(video, desired_title: str) -> tuple[bool, str]:
    source_path = str(video.filepath or "").strip()
    if video.url:
        return False, "链接导入视频不执行本地重命名"
    if not source_path:
        return False, "缺少原始文件路径"
    if not os.path.exists(source_path):
        return False, "原始文件不存在"

    ext = os.path.splitext(video.filename or source_path)[1] or os.path.splitext(source_path)[1]
    safe_title = build_safe_filename_stem(desired_title, fallback=f"video_{video.id}")
    target_filename = f"{safe_title}{ext.lower()}"
    target_path = os.path.join(os.path.dirname(source_path), target_filename)

    if os.path.abspath(target_path) != os.path.abspath(source_path):
        if os.path.exists(target_path):
            safe_title = build_safe_filename_stem(f"{safe_title}_{video.id}", fallback=f"video_{video.id}")
            target_filename = f"{safe_title}{ext.lower()}"
            target_path = os.path.join(os.path.dirname(source_path), target_filename)

        if os.path.exists(target_path):
            return False, "目标文件名已存在，跳过重命名"

        os.replace(source_path, target_path)

    video.title = safe_title
    video.filename = target_filename
    video.filepath = target_path
    return True, safe_title


def get_device():
    """获取最佳计算设备 - 自动检测 CUDA/MPS/CPU"""
    return get_whisper_device()


def clear_gpu_cache():
    """清理 GPU 缓存，释放显存"""
    clear_whisper_device_cache()
    gc.collect()


def format_elapsed_label(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remain = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{remain:02d}"
    return f"{minutes:02d}:{remain:02d}"


def estimate_transcription_seconds(duration_seconds: float, model_name: str) -> float:
    """估算转录耗时，用于提供连续进度反馈。"""
    duration = max(0.0, float(duration_seconds or 0.0))
    normalized_model = str(model_name or "").strip().lower()

    multiplier_map = {
        "tiny": 0.35,
        "base": 0.55,
        "small": 0.9,
        "medium": 1.4,
        "large": 2.0,
        "turbo": 0.45,
    }
    multiplier = multiplier_map.get(normalized_model, 0.75)
    estimated = duration * multiplier
    return max(estimated, 20.0)


def generate_video_info(video_path: str) -> dict:
    """获取视频信息（OpenCV 优先，ffmpeg/ffprobe 回退）"""
    # 尝试 OpenCV
    try:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps if fps > 0 else 0
            cap.release()
            if width > 0 and height > 0:
                return {
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "frame_count": frame_count,
                    "duration": duration,
                }
    except Exception as e:
        logger.warning(f"OpenCV 获取视频信息失败，尝试 ffprobe: {str(e)}")

    # 回退：使用 ffmpeg stderr 解析视频信息
    try:
        import re
        import subprocess

        result = subprocess.run(["ffmpeg", "-i", video_path], capture_output=True, text=True, timeout=30)
        stderr = result.stderr
        duration_match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
        duration = 0
        if duration_match:
            h, m, s = duration_match.groups()
            duration = int(h) * 3600 + int(m) * 60 + float(s)

        width, height, fps = 0, 0, 0
        for line in stderr.split(chr(10)):
            if "Stream" in line and "Video" in line:
                dim_match = re.search(r"(\d+)x(\d+)", line)
                if dim_match:
                    width, height = int(dim_match.group(1)), int(dim_match.group(2))
                fps_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:fps|tbr)", line)
                if fps_match:
                    fps = float(fps_match.group(1))
                break

        logger.info(
            "ffmpeg 提取视频信息成功 | path=%s | dur=%.1fs | %dx%d | %.2ffps",
            video_path,
            duration,
            width,
            height,
            fps,
        )
        return {
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": int(duration * fps) if fps > 0 else 0,
            "duration": duration,
        }
    except Exception as e:
        logger.error(f"ffmpeg 获取视频信息失败: {str(e)}")
        return None


def is_placeholder_file(filepath: str) -> bool:
    """检查是否为占位文件"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read(100)
            return "慕课视频链接" in content or "无法下载慕课视频" in content
    except (UnicodeDecodeError, Exception):
        return False


def generate_preview_image(video_path: str, preview_path: str) -> bool:
    """生成视频预览图（OpenCV 优先，ffmpeg 回退）"""
    # 检查是否为占位文件
    if is_placeholder_file(video_path):
        try:
            import cv2
            import numpy as np

            img = np.zeros((360, 640, 3), dtype=np.uint8)
            img[:, :] = (25, 55, 125)
            font = cv2.FONT_HERSHEY_SIMPLEX
            cv2.putText(
                img,
                "Placeholder Video",
                (160, 180),
                font,
                1,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                img,
                "Please replace manually",
                (140, 220),
                font,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imwrite(preview_path, img)
            return True
        except Exception:
            return True  # 占位文件无需实际预览

    # 尝试 OpenCV
    try:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                cv2.imwrite(preview_path, frame)
                return True
    except Exception as e:
        logger.warning(f"OpenCV 生成预览图失败，尝试 ffmpeg: {str(e)}")

    # 回退：使用 ffmpeg 提取第一帧
    try:
        import subprocess

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-vframes",
                "1",
                "-q:v",
                "2",
                preview_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and os.path.exists(preview_path):
            return True
    except Exception as e:
        logger.warning(f"ffmpeg 生成预览图也失败: {str(e)}")

    return False


def extract_audio(video_path: str, audio_path: str) -> bool:
    """从视频中提取音频"""
    try:
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-threads",
            "4",
            audio_path,
        ]

        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=300)
        return os.path.exists(audio_path)
    except Exception as e:
        logger.error(f"提取音频失败: {str(e)}")
        return False


def should_retry_transcribe_on_cpu(device: str, error: Exception) -> bool:
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


def transcribe_with_whisper(
    audio_path: str,
    model_name: str,
    language: str,
    model_path: str,
    *,
    force_device: str = "",
) -> dict:
    """使用 Whisper 转录音频"""
    device = force_device or get_device()
    try:
        logger.info("调用 Whisper 运行时执行转录 | model=%s | device=%s", model_name, device)
        result = transcribe_audio_with_whisper(
            audio_path,
            model_name,
            language,
            model_path,
            force_device=device,
        )
        clear_gpu_cache()
        return result
    except Exception as e:
        logger.error(f"转录失败: {str(e)}")
        clear_gpu_cache()
        if should_retry_transcribe_on_cpu(device, e):
            logger.warning("MPS 转录失败，自动切换到 CPU 重试 | model=%s", model_name)
            return transcribe_with_whisper(
                audio_path,
                model_name,
                language,
                model_path,
                force_device="cpu",
            )
        return None


def transcribe_with_live_progress(
    *,
    video_id: int,
    audio_path: str,
    model_name: str,
    language: str,
    model_path: str,
    duration_seconds: float,
):
    """执行 Whisper 转录，并在长阶段内持续回写进度。"""
    result_holder = {"result": None, "error": None}
    estimated_seconds = estimate_transcription_seconds(duration_seconds, model_name)
    started_at = time.time()

    def worker():
        try:
            result_holder["result"] = transcribe_with_whisper(audio_path, model_name, language, model_path)
        except Exception as exc:  # pragma: no cover - 保护线程内异常
            result_holder["error"] = exc

    thread = threading.Thread(target=worker, name=f"whisper-transcribe-{video_id}", daemon=True)
    thread.start()

    update_video_status(video_id, "processing", 62.0, "语音识别中")

    while thread.is_alive():
        elapsed = time.time() - started_at
        progress_ratio = min(elapsed / estimated_seconds, 1.0)
        progress = (
            TRANSCRIPTION_PROGRESS_START + (TRANSCRIPTION_PROGRESS_END - TRANSCRIPTION_PROGRESS_START) * progress_ratio
        )
        step = f"语音识别中（已运行 {format_elapsed_label(elapsed)}）"
        update_video_status(
            video_id,
            "processing",
            round(min(progress, TRANSCRIPTION_PROGRESS_END), 1),
            step,
        )
        thread.join(timeout=TRANSCRIPTION_POLL_SECONDS)

    thread.join()

    if result_holder["error"] is not None:
        raise result_holder["error"]

    return result_holder["result"]


def save_subtitles(result: dict, srt_path: str, txt_path: str):
    """保存字幕文件"""
    try:
        from whisper.utils import WriteSRT, WriteTXT

        repaired_result = dict(result or {})
        repaired_result["text"] = repair_mojibake_text(str(repaired_result.get("text") or ""))
        repaired_segments = []
        for segment in repaired_result.get("segments") or []:
            repaired_segment = dict(segment)
            repaired_segment["text"] = repair_mojibake_text(str(repaired_segment.get("text") or ""))
            repaired_segments.append(repaired_segment)
        repaired_result["segments"] = repaired_segments

        with open(srt_path, "w", encoding="utf-8-sig") as srt:
            WriteSRT(None).write_result(repaired_result, srt)

        with open(txt_path, "w", encoding="utf-8-sig") as txt:
            WriteTXT(None).write_result(repaired_result, txt)

        logger.info(f"字幕已保存: {os.path.basename(srt_path)}")
        return True
    except Exception as e:
        logger.error(f"保存字幕失败: {str(e)}")
        return False


def sync_subtitles_to_db(db, video_id: int, result: dict, language: str) -> int:
    """将 Whisper 分段结果写入字幕表。

    若当前数据库未启用 subtitles 表，则跳过，不影响主处理流程。
    """
    from app.models.subtitle import Subtitle

    segments = result.get("segments") or []
    if not segments:
        logger.warning("转录结果不包含 segments，跳过字幕落库 | video_id=%s", video_id)
        return 0

    bind = db.get_bind()
    if bind is None:
        logger.warning("当前数据库连接不可用，跳过字幕落库 | video_id=%s", video_id)
        return 0

    try:
        if not inspect(bind).has_table(Subtitle.__tablename__):
            logger.warning("数据库缺少 subtitles 表，跳过字幕落库 | video_id=%s", video_id)
            return 0
    except Exception as exc:
        logger.warning(
            "检查 subtitles 表失败，跳过字幕落库 | video_id=%s | error=%s",
            video_id,
            exc,
        )
        return 0

    subtitle_rows = []
    for segment in segments:
        text = repair_mojibake_text(str(segment.get("text") or "")).strip()
        if not text:
            continue

        start_time = float(segment.get("start") or 0.0)
        end_time = float(segment.get("end") or start_time)
        if end_time < start_time:
            end_time = start_time

        subtitle_rows.append(
            Subtitle(
                video_id=video_id,
                start_time=start_time,
                end_time=end_time,
                text=text,
                source="asr",
                language=language or "zh",
            )
        )

    if not subtitle_rows:
        logger.warning("未生成有效字幕分段，跳过字幕落库 | video_id=%s", video_id)
        return 0

    try:
        db.query(Subtitle).filter(Subtitle.video_id == video_id).delete()
        db.add_all(subtitle_rows)
        db.commit()
        logger.info("字幕已写入数据库 | video_id=%s | count=%s", video_id, len(subtitle_rows))
        return len(subtitle_rows)
    except Exception as exc:
        db.rollback()
        logger.error("字幕写入数据库失败 | video_id=%s | error=%s", video_id, exc)
        return 0


def update_video_status(video_id: int, status: str, progress: float, step: str, **kwargs):
    """更新视频状态 (在子进程中创建新的数据库连接)"""
    # 动态导入以避免跨进程问题
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.models.video import Video, VideoStatus

    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        connect_args=({"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}),
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    video_path = ""
    temp_audio_path = ""

    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        if video:
            video.status = VideoStatus(status) if isinstance(status, str) else status
            video.process_progress = progress
            video.current_step = step

            for key, value in kwargs.items():
                if hasattr(video, key):
                    setattr(video, key, value)

            db.commit()
    finally:
        db.close()
        engine.dispose()


def start_indexing_async(
    video_id: int,
    user_id: int,
    subtitle_path: str,
    embedding_backend: str = None,
):
    """
    在字幕文件就绪后启动内嵌语义索引

    用途：当字幕已生成且可用时，在字幕生成完成后立即启动异步索引任务，
         使其与后续的摘要/标签生成兼容，从而降低用户感知的总处理时间

    Args:
        video_id: 视频 ID
        user_id: 用户 ID
        subtitle_path: 已生成的字幕文件路径
        embedding_backend: 向量后端（如果为 None，从配置读取）
    """
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.core.executor import submit_task
    from app.models.vector_index import VectorIndex, VectorIndexStatus

    db = SessionLocal()
    try:
        backend = embedding_backend or settings.SEARCH_BACKEND

        # 检查字幕文件是否存在
        if not subtitle_path or not os.path.exists(subtitle_path):
            logger.warning(
                f"跳过内嵌索引启动：字幕文件不可用 | " f"video_id={video_id} | subtitle_path={subtitle_path}"
            )
            return

        # 获取或创建 VectorIndex 记录
        vector_index = (
            db.query(VectorIndex).filter(VectorIndex.video_id == video_id, VectorIndex.user_id == user_id).first()
        )

        if vector_index is None:
            collection_name = f"user_{user_id}_video_{video_id}_chunks"
            vector_index = VectorIndex(
                video_id=video_id,
                user_id=user_id,
                collection_name=collection_name,
                embedding_backend=backend,
                embedding_model=(settings.SEARCH_LOCAL_MODEL if backend == "local" else None),
                status=VectorIndexStatus.PENDING,
            )
            db.add(vector_index)
            db.commit()
        else:
            # 已有记录时，重置状态为 PENDING（准备新一轮索引）
            # 这确保 wait_for_indexing_ready() 不会被旧状态误导
            vector_index.status = VectorIndexStatus.PENDING
            vector_index.error_message = None
            vector_index.chunk_count = 0
            db.commit()

        # 导入索引函数
        from app.tasks.vector_indexing import index_video_inline

        logger.info(f"提交内嵌语义索引任务 | video_id={video_id} | " f"user_id={user_id} | backend={backend}")
        # 使用后台任务执行器提交索引任务
        submit_task(index_video_inline, video_id, user_id, subtitle_path, backend)

    except Exception as e:
        logger.warning(f"启动内嵌索引失败（不中断视频处理）| " f"video_id={video_id} | error={e}")
    finally:
        db.close()


def wait_for_indexing_ready(video_id: int, user_id: int, timeout_seconds: int = 30) -> bool:
    """
    等待语义索引完成或超时

    用途：在设置 VideoStatus.COMPLETED 之前，验证语义索引是否已完成或失败。
         根据 SEARCH_INLINE_INDEX_FAIL_POLICY 配置决定是否允许主流程继续。

    Args:
        video_id: 视频 ID
        user_id: 用户 ID
        timeout_seconds: 最大等待超时（秒）；-1 表示不等待

    Returns:
        bool: True 表示索引已完成或已跳过，False 表示索引失败且策略要求中止
    """
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.vector_index import VectorIndex, VectorIndexStatus

    # 如果超时设置为负数，表示不等待
    if timeout_seconds < 0:
        logger.info(f"跳过索引等待（timeout_seconds={timeout_seconds}）")
        return True

    db = SessionLocal()
    try:
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logger.warning(f"等待语义索引超时（{timeout_seconds}s）| video_id={video_id}")
                # 超时时，根据失败策略处理
                if settings.SEARCH_INLINE_INDEX_FAIL_POLICY == "require_index_success":
                    return False
                else:
                    return True  # mark_completed_without_index

            vector_index = (
                db.query(VectorIndex).filter(VectorIndex.video_id == video_id, VectorIndex.user_id == user_id).first()
            )

            # 未找到索引记录，表示可能未启动内嵌模式
            if not vector_index:
                logger.debug(f"未找到 VectorIndex 记录，跳过等待 | video_id={video_id}")
                return True

            # 索引已完成
            if vector_index.status == VectorIndexStatus.COMPLETED:
                logger.info(f"语义索引完成 | video_id={video_id} | " f"chunk_count={vector_index.chunk_count}")
                return True

            # 索引失败
            if vector_index.status == VectorIndexStatus.FAILED:
                logger.warning(f"语义索引失败 | video_id={video_id} | " f"error={vector_index.error_message}")
                if settings.SEARCH_INLINE_INDEX_FAIL_POLICY == "require_index_success":
                    return False
                else:
                    return True  # mark_completed_without_index

            # 仍在处理中，等待一段时间后重试
            time.sleep(1)

    except Exception as e:
        logger.warning(f"检查索引状态异常 | video_id={video_id} | error={e}")
        # 异常时，根据配置决定是否允许继续
        if settings.SEARCH_INLINE_INDEX_FAIL_POLICY == "require_index_success":
            return False
        else:
            return True

    finally:
        db.close()


def process_video_task(
    video_id: int,
    language: str = "zh",
    model: str = "base",
    *,
    auto_generate_summary: bool = True,
    auto_generate_tags: bool = True,
    summary_style: str = "study",
):
    """
    视频处理任务（在后台执行器中执行）

    Args:
        video_id: 视频ID
        language: 语言代码 (zh, en, ja 等)
        model: Whisper 模型名称 (tiny, base, small, medium, large, turbo)

    Returns:
        dict: 处理结果
    """
    logger.info(
        "开始处理视频 | ID: %s | 语言: %s | 模型: %s | auto_summary=%s | auto_tags=%s | summary_style=%s",
        video_id,
        language,
        model,
        auto_generate_summary,
        auto_generate_tags,
        summary_style,
    )

    # 动态导入以避免跨进程问题
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.models.video import Video, VideoStatus

    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        connect_args=({"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}),
    )
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # 获取视频信息
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            logger.error(f"视频不存在 | ID: {video_id}")
            return {"status": "failed", "message": "视频不存在"}

        video_path = str(video.filepath or "")

        # 更新状态为处理中
        video.status = VideoStatus.PROCESSING
        video.process_progress = 0.0
        video.current_step = "初始化处理"
        video.error_message = None
        if auto_generate_summary:
            video.summary = None
        if auto_generate_tags:
            video.tags = None
        db.commit()

        # 检查是否为占位文件
        is_placeholder = is_placeholder_file(video_path)

        if is_placeholder:
            logger.warning(f"检测到占位文件: {video_path}")
            video.process_progress = 50.0
            video.current_step = "处理占位文件"
            db.commit()

        # Step 1: 生成预览图 (10%)
        video.process_progress = 10.0
        video.current_step = "生成预览图"
        db.commit()

        preview_dir = os.path.join(settings.UPLOAD_FOLDER, "previews")
        os.makedirs(preview_dir, exist_ok=True)
        preview_filename = f"preview_{video_id}.jpg"
        preview_path = os.path.join(preview_dir, preview_filename)

        if generate_preview_image(video_path, preview_path):
            video.preview_filename = preview_filename
            video.preview_filepath = preview_path
            db.commit()
        else:
            logger.warning("生成预览图失败，继续处理视频 | video_id=%s", video_id)

        # Step 2: 提取视频信息 (30%)
        video.process_progress = 30.0
        video.current_step = "提取视频信息"
        db.commit()

        if is_placeholder:
            video.duration = 0
            video.fps = 0
            video.width = 640
            video.height = 360
            video.frame_count = 0
        else:
            video_info = generate_video_info(video_path)
            if video_info:
                video.width = video_info["width"]
                video.height = video_info["height"]
                video.fps = video_info["fps"]
                video.frame_count = video_info["frame_count"]
                video.duration = video_info["duration"]
            else:
                video.status = VideoStatus.FAILED
                video.current_step = "获取视频信息失败"
                db.commit()
                return {"status": "failed", "message": "获取视频信息失败"}

        db.commit()

        # 如果是占位文件，直接完成
        if is_placeholder:
            video.status = VideoStatus.COMPLETED
            video.process_progress = 100.0
            video.current_step = "完成"
            db.commit()
            return {"status": "success", "message": "占位文件处理完成"}

        # Step 3: 提取音频 (50%)
        video.process_progress = 50.0
        video.current_step = "提取音频"
        db.commit()

        audio_dir = os.path.join(settings.UPLOAD_FOLDER, "audio_temp")
        os.makedirs(audio_dir, exist_ok=True)
        base_filename = os.path.splitext(os.path.basename(video_path))[0]
        audio_path = os.path.join(audio_dir, f"{base_filename}.wav")

        input_file = video_path
        if extract_audio(video_path, audio_path):
            input_file = audio_path
            temp_audio_path = audio_path
            logger.info("音频提取成功")
        else:
            logger.warning("音频提取失败，使用原视频文件")

        # Step 4: 转录 (60-85%)
        video.process_progress = TRANSCRIPTION_PROGRESS_START
        video.current_step = f"准备语音识别（{model}）"
        db.commit()

        result = transcribe_with_live_progress(
            video_id=video_id,
            audio_path=input_file,
            model_name=model,
            language=language,
            model_path=settings.WHISPER_MODEL_PATH,
            duration_seconds=video.duration or 0.0,
        )

        if not result:
            video.status = VideoStatus.FAILED
            video.current_step = f"转录失败（{model}）"
            db.commit()
            return {"status": "failed", "message": "转录失败"}

        transcript_text = extract_transcript_text(result)

        # Step 5: 保存字幕 (85-95%)
        video.process_progress = 85.0
        video.current_step = "生成字幕文件"
        db.commit()

        subtitle_dir = os.path.join(settings.UPLOAD_FOLDER, "subtitles")
        os.makedirs(subtitle_dir, exist_ok=True)
        srt_path = os.path.join(subtitle_dir, f"{base_filename}.srt")
        txt_path = os.path.join(subtitle_dir, f"{base_filename}.txt")

        if save_subtitles(result, srt_path, txt_path):
            video.subtitle_filepath = srt_path
        else:
            logger.warning("保存字幕失败，但继续完成处理")

        sync_subtitles_to_db(db, video_id, result, language)

        # 如果启用了内嵌索引模式，则在字幕就绪后立即启动（与后续摘要/标签并行）
        inline_indexing_started = False
        if (
            settings.SEARCH_ENABLED
            and settings.SEARCH_AUTO_INDEX_NEW_VIDEOS
            and settings.SEARCH_INDEX_STARTUP_MODE == "inline_after_subtitle"
            and video.subtitle_filepath
            and os.path.exists(video.subtitle_filepath)
        ):
            try:
                start_indexing_async(
                    video_id=video_id,
                    user_id=video.user_id,
                    subtitle_path=video.subtitle_filepath,
                    embedding_backend=settings.SEARCH_BACKEND,
                )
                inline_indexing_started = True
                logger.info(f"已启动内嵌语义索引任务 | video_id={video_id}")
            except Exception as e:
                logger.warning(f"启动内嵌索引任务失败（将继续处理）| video_id={video_id} | error={e}")
                inline_indexing_started = False

        if auto_generate_summary or auto_generate_tags:
            from app.services.video_content_service import (
                generate_primary_topic_name,
                generate_video_summary,
                generate_video_tags,
                normalize_summary_style,
            )

            normalized_summary_style = normalize_summary_style(summary_style)
            if auto_generate_summary or auto_generate_tags:
                video.process_progress = 92.0
                video.current_step = f"生成摘要（{normalized_summary_style}）"
                db.commit()

                summary_result = generate_video_summary(
                    video_id,
                    video.subtitle_filepath or "",
                    transcript_text=transcript_text,
                    title=video.title or "",
                    style=normalized_summary_style,
                )
                if summary_result.get("success"):
                    video.summary = summary_result["summary"]
                    db.commit()
                else:
                    logger.warning(
                        "摘要生成失败，跳过写回 | video_id=%s | error=%s",
                        video_id,
                        summary_result.get("error"),
                    )

            if auto_generate_tags and video.summary:
                video.process_progress = 97.0
                video.current_step = "提取学习标签"
                db.commit()

                tag_result = generate_video_tags(video_id, video.summary, title=video.title or "")
                if tag_result.get("success"):
                    video.tags = json.dumps(tag_result["tags"], ensure_ascii=False)
                    db.commit()
                else:
                    logger.warning(
                        "标签生成失败，跳过写回 | video_id=%s | error=%s",
                        video_id,
                        tag_result.get("error"),
                    )

            if not video.url and (video.summary or video.tags):
                resolved_tags = []
                if video.tags:
                    try:
                        resolved_tags = json.loads(video.tags)
                    except Exception as exc:
                        logger.warning(
                            "解析视频标签失败，重命名时忽略标签 | video_id=%s | error=%s",
                            video_id,
                            exc,
                        )

                title_result = generate_primary_topic_name(
                    video.summary or "",
                    tags=resolved_tags,
                    title=video.title or "",
                )
                if title_result.get("success"):
                    try:
                        renamed, resolved_title = rename_local_video_file(video, title_result["name"])
                        if renamed:
                            db.commit()
                            logger.info(
                                "本地上传视频已按内容重命名 | video_id=%s | title=%s | filename=%s",
                                video_id,
                                resolved_title,
                                video.filename,
                            )
                        else:
                            logger.info(
                                "本地上传视频跳过重命名 | video_id=%s | reason=%s",
                                video_id,
                                resolved_title,
                            )
                    except Exception as exc:
                        db.rollback()
                        logger.warning(
                            "本地上传视频重命名失败 | video_id=%s | error=%s",
                            video_id,
                            exc,
                        )
                else:
                    logger.warning(
                        "视频主标题生成失败，保留原文件名 | video_id=%s | error=%s",
                        video_id,
                        title_result.get("error"),
                    )

        # 清理临时音频文件
        if input_file != video_path and os.path.exists(input_file):
            try:
                os.remove(input_file)
            except Exception:
                pass

        # 如果启用了内嵌索引模式且已启动，则等待索引完成
        if (
            settings.SEARCH_ENABLED
            and settings.SEARCH_AUTO_INDEX_NEW_VIDEOS
            and settings.SEARCH_INDEX_STARTUP_MODE == "inline_after_subtitle"
            and inline_indexing_started
        ):
            try:
                video.process_progress = 98.0
                video.current_step = "等待语义索引完成中..."
                db.commit()

                # 等待索引完成
                indexing_ready = wait_for_indexing_ready(
                    video_id=video_id,
                    user_id=video.user_id,
                    timeout_seconds=settings.SEARCH_INLINE_INDEX_WAIT_TIMEOUT_SECONDS,
                )

                if not indexing_ready:
                    logger.warning(
                        f"等待索引完成失败，根据配置决定是否继续 | "
                        f"video_id={video_id} | policy={settings.SEARCH_INLINE_INDEX_FAIL_POLICY}"
                    )
                    if settings.SEARCH_INLINE_INDEX_FAIL_POLICY == "require_index_success":
                        video.status = VideoStatus.FAILED
                        video.current_step = "语义索引失败，处理中止"
                        db.commit()
                        logger.error(f"视频处理因索引失败而中止 | video_id={video_id}")
                        return {"status": "failed", "message": "语义索引失败"}
                    # 否则继续标记为 COMPLETED

            except Exception as e:
                logger.warning(f"等待索引完成时发生异常（将继续标记处理完成）| " f"video_id={video_id} | error={e}")

        # 完成
        video.status = VideoStatus.COMPLETED
        video.process_progress = 100.0
        video.current_step = f"处理完成（{model}）"
        video.error_message = None
        db.commit()

        # 如果使用旧的 after_video_completed 模式，则提交异步索引任务
        if (
            settings.SEARCH_ENABLED
            and settings.SEARCH_AUTO_INDEX_NEW_VIDEOS
            and settings.SEARCH_INDEX_STARTUP_MODE == "after_video_completed"
        ):
            try:
                from app.core.executor import submit_task
                from app.tasks.vector_indexing import index_video_for_search

                logger.info(f"提交异步语义搜索索引任务（after_video_completed 模式）| video_id={video_id}")
                submit_task(
                    index_video_for_search,
                    video_id,
                    video.user_id,
                    settings.SEARCH_BACKEND,
                )
            except Exception as e:
                logger.warning(f"提交索引任务失败（不中断视频处理）| video_id={video_id} | error={e}")

        logger.info(f"视频处理完成 | ID: {video_id}")
        return {"status": "success", "message": "视频处理成功"}

    except Exception as e:
        logger.error(f"处理视频失败 | ID: {video_id} | 错误: {str(e)}")

        try:
            video = db.query(Video).filter(Video.id == video_id).first()
            if video:
                video.status = VideoStatus.FAILED
                video.process_progress = 0.0
                video.current_step = f"处理失败（{model}）: {str(e)}"
                db.commit()
        except Exception:
            pass

        clear_gpu_cache()
        return {"status": "failed", "message": f"处理视频失败: {str(e)}"}

    finally:
        if temp_audio_path and temp_audio_path != video_path and os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.debug(
                    "清理临时音频失败（忽略）| path=%s | error=%s",
                    temp_audio_path,
                    cleanup_exc,
                )
        db.close()
        engine.dispose()


def cleanup_video_task(video_id: int, delete_db_record: bool = True) -> dict:
    """
    清理视频文件任务

    Args:
        video_id: 视频ID

    Returns:
        dict: 清理结果
    """
    logger.info(f"开始清理视频 | ID: {video_id}")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.models.qa import Question
    from app.models.video import Video

    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        connect_args=({"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}),
    )
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            return {"status": "error", "message": f"未找到ID为{video_id}的视频"}

        # 删除关联的问题记录
        db.query(Question).filter(Question.video_id == video_id).delete()

        # 删除文件
        files_to_delete = [
            video.filepath,
            video.processed_filepath,
            video.preview_filepath,
            video.subtitle_filepath,
        ]

        for filepath in files_to_delete:
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    logger.warning(f"删除文件失败: {filepath} - {str(e)}")

        if delete_db_record:
            # 历史行为：删除数据库记录
            db.delete(video)
        else:
            # 软删除模式：保留数据库记录，仅清空文件路径
            video.filepath = None
            video.processed_filepath = None
            video.preview_filepath = None
            video.subtitle_filepath = None

        db.commit()

        logger.info(f"视频清理完成 | ID: {video_id} | delete_db_record={delete_db_record}")
        return {"status": "success", "message": "视频文件清理完成"}

    except Exception as e:
        logger.error(f"清理视频文件时出错: {str(e)}")
        return {"status": "error", "message": str(e)}

    finally:
        db.close()
        engine.dispose()
