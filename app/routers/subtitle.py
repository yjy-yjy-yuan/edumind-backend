"""字幕管理路由 - FastAPI 版本"""

import io
import json
import logging
import os
from typing import Optional

from app.core.config import settings
from app.core.database import get_db
from app.models.subtitle import Subtitle
from app.models.video import Video
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


def format_seconds_to_srt_time(seconds: float) -> str:
    """转换秒数为 SRT 时间格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_seconds_to_vtt_time(seconds: float) -> str:
    """转换秒数为 VTT 时间格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def format_seconds_to_display_time(seconds: float) -> str:
    """转换秒数为显示时间"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def parse_srt_content(content: str) -> list:
    """解析 SRT 文件内容"""
    subtitle_blocks = content.strip().split("\n\n")
    subtitles = []

    for block in subtitle_blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            time_line = lines[1]
            if "-->" in time_line:
                start_str, end_str = time_line.split("-->")

                # 解析开始时间
                start_parts = start_str.strip().replace(",", ".").split(":")
                if len(start_parts) == 2:
                    start_time = int(start_parts[0]) * 60 + float(start_parts[1])
                else:
                    start_time = int(start_parts[0]) * 3600 + int(start_parts[1]) * 60 + float(start_parts[2])

                # 解析结束时间
                end_parts = end_str.strip().replace(",", ".").split(":")
                if len(end_parts) == 2:
                    end_time = int(end_parts[0]) * 60 + float(end_parts[1])
                else:
                    end_time = int(end_parts[0]) * 3600 + int(end_parts[1]) * 60 + float(end_parts[2])

                text = "\n".join(lines[2:])
                subtitles.append({"start_time": start_time, "end_time": end_time, "text": text})

    return subtitles


@router.get("/videos/{video_id}/subtitles")
async def get_video_subtitles(video_id: int, db: Session = Depends(get_db)):
    """获取视频的所有字幕"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail=f"未找到ID为{video_id}的视频")

    subtitles = db.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.start_time.asc()).all()

    if subtitles:
        subtitle_list = [
            {
                "id": sub.id,
                "start_time": sub.start_time,
                "end_time": sub.end_time,
                "text": sub.text,
                "language": sub.language,
            }
            for sub in subtitles
        ]
    else:
        subtitle_list = []
        if video.subtitle_filepath and os.path.exists(video.subtitle_filepath):
            try:
                with open(video.subtitle_filepath, "r", encoding="utf-8") as handle:
                    subtitle_content = handle.read()
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"读取字幕文件时出错: {str(exc)}")

            subtitle_list = [
                {
                    "id": None,
                    "start_time": item["start_time"],
                    "end_time": item["end_time"],
                    "text": item["text"],
                    "language": "zh",
                }
                for item in parse_srt_content(subtitle_content)
            ]

    return {
        "status": "success",
        "video_id": video_id,
        "video_status": video.status.value if hasattr(video.status, "value") else video.status,
        "subtitles": subtitle_list,
    }


@router.get("/videos/{video_id}/subtitles/semantic-merged")
async def get_merged_subtitles(
    video_id: int, force_refresh: bool = False, format: Optional[str] = None, db: Session = Depends(get_db)
):
    """获取语义合并后的字幕"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    if not video.subtitle_filepath or not os.path.exists(video.subtitle_filepath):
        raise HTTPException(status_code=404, detail="字幕文件不存在")

    # 构建缓存文件路径
    cache_dir = os.path.join(settings.UPLOAD_FOLDER, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    video_name = video.filename.rsplit(".", 1)[0] if video.filename else f"video_{video_id}"
    cache_file = os.path.join(cache_dir, f"{video_name}_semantic.json")

    merged_subtitles = None

    # 尝试从缓存读取
    if os.path.exists(cache_file) and not force_refresh:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                merged_subtitles = json.load(f)
        except Exception as e:
            logger.error(f"读取缓存文件时出错: {str(e)}")

    # 如果没有缓存，重新生成
    if merged_subtitles is None:
        try:
            with open(video.subtitle_filepath, "r", encoding="utf-8") as f:
                subtitle_content = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"读取字幕文件时出错: {str(e)}")

        subtitles = parse_srt_content(subtitle_content)

        if not subtitles:
            return []

        # 尝试使用语义合并
        try:
            from app.utils.semantic_utils import merge_subtitles_by_semantics_ollama

            merged_subtitles = merge_subtitles_by_semantics_ollama(subtitles)
        except ImportError:
            # 如果没有语义工具，返回原始字幕
            merged_subtitles = subtitles

        # 保存缓存
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(merged_subtitles, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存缓存文件时出错: {str(e)}")

    # 如果请求特定格式，转换并返回
    if format:
        if not merged_subtitles:
            raise HTTPException(status_code=404, detail="没有可用的合并字幕")

        if format == "srt":
            content = ""
            for i, sub in enumerate(merged_subtitles):
                start = format_seconds_to_srt_time(sub["start_time"])
                end = format_seconds_to_srt_time(sub["end_time"])
                content += f"{i + 1}\n{start} --> {end}\n{sub['text']}\n\n"
            mimetype = "text/plain"
        elif format == "vtt":
            content = "WEBVTT\n\n"
            for i, sub in enumerate(merged_subtitles):
                start = format_seconds_to_vtt_time(sub["start_time"])
                end = format_seconds_to_vtt_time(sub["end_time"])
                content += f"{i + 1}\n{start} --> {end}\n{sub['text']}\n\n"
            mimetype = "text/vtt"
        elif format == "txt":
            content = ""
            for sub in merged_subtitles:
                start = format_seconds_to_display_time(sub["start_time"])
                end = format_seconds_to_display_time(sub["end_time"])
                title = sub.get("title", "")
                content += f"[{start} - {end}] {title}\n{sub['text']}\n\n"
            mimetype = "text/plain"
        else:
            raise HTTPException(status_code=400, detail=f"不支持的格式: {format}")

        return Response(
            content=content.encode("utf-8"),
            media_type=mimetype,
            headers={"Content-Disposition": f"attachment; filename=merged-{video.title or video_id}.{format}"},
        )

    return merged_subtitles


@router.post("/videos/{video_id}/subtitles/semantic-merge")
async def trigger_semantic_merge(video_id: int, db: Session = Depends(get_db)):
    """触发字幕的语义合并处理"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    if not video.subtitle_filepath or not os.path.exists(video.subtitle_filepath):
        raise HTTPException(status_code=404, detail="字幕文件不存在")

    try:
        with open(video.subtitle_filepath, "r", encoding="utf-8") as f:
            subtitle_content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取字幕文件时出错: {str(e)}")

    subtitles = parse_srt_content(subtitle_content)
    if not subtitles:
        raise HTTPException(status_code=400, detail="没有解析出有效的字幕")

    # 构建缓存路径
    cache_dir = os.path.join(settings.UPLOAD_FOLDER, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    video_name = video.filename.rsplit(".", 1)[0] if video.filename else f"video_{video_id}"
    cache_file = os.path.join(cache_dir, f"{video_name}_semantic.json")

    # 删除旧缓存
    if os.path.exists(cache_file):
        os.remove(cache_file)

    # 执行语义合并
    try:
        from app.utils.semantic_utils import merge_subtitles_by_semantics_ollama

        merged_subtitles = merge_subtitles_by_semantics_ollama(subtitles)
    except ImportError:
        merged_subtitles = subtitles

    if not merged_subtitles:
        raise HTTPException(status_code=500, detail="合并字幕失败，结果为空")

    # 保存缓存
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(merged_subtitles, f, ensure_ascii=False, indent=2)

    return {"success": True, "message": "语义合并处理完成", "count": len(merged_subtitles)}


@router.get("/videos/{video_id}/subtitles/export")
async def export_subtitles(video_id: int, format: str = "srt", db: Session = Depends(get_db)):
    """导出字幕文件"""
    if format not in ["srt", "vtt", "txt"]:
        raise HTTPException(status_code=400, detail="不支持的字幕格式")

    subtitles = db.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.start_time).all()
    if not subtitles:
        raise HTTPException(status_code=404, detail="未找到字幕数据")

    video = db.query(Video).filter(Video.id == video_id).first()

    # 转换格式
    if format == "srt":
        content = ""
        for i, sub in enumerate(subtitles):
            start = format_seconds_to_srt_time(sub.start_time)
            end = format_seconds_to_srt_time(sub.end_time)
            content += f"{i + 1}\n{start} --> {end}\n{sub.text}\n\n"
        mimetype = "application/x-subrip"
    elif format == "vtt":
        content = "WEBVTT\n\n"
        for i, sub in enumerate(subtitles):
            start = format_seconds_to_vtt_time(sub.start_time)
            end = format_seconds_to_vtt_time(sub.end_time)
            content += f"{i + 1}\n{start} --> {end}\n{sub.text}\n\n"
        mimetype = "text/vtt"
    else:
        content = ""
        for sub in subtitles:
            content += f"{sub.text}\n"
        mimetype = "text/plain"

    filename = f"{video.title or video_id}_{video_id}.{format}"

    return Response(
        content=content.encode("utf-8"),
        media_type=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/videos/{video_id}/subtitles/{subtitle_id}")
async def update_subtitle(
    video_id: int,
    subtitle_id: int,
    text: Optional[str] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """更新字幕内容"""
    subtitle = db.query(Subtitle).filter(Subtitle.id == subtitle_id, Subtitle.video_id == video_id).first()
    if not subtitle:
        raise HTTPException(status_code=404, detail="未找到指定字幕")

    if text is not None:
        subtitle.text = text
    if start_time is not None:
        subtitle.start_time = start_time
    if end_time is not None:
        subtitle.end_time = end_time

    db.commit()
    return {"status": "success", "message": "字幕更新成功", "subtitle": subtitle.to_dict()}
