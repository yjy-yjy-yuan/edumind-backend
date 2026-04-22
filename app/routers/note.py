"""笔记管理路由 - FastAPI 版本"""

import io
import json
import logging
import zipfile
from typing import List
from typing import Optional

import jieba.analyse
from app.core.database import get_db
from app.models.note import Note
from app.models.note import NoteTimestamp
from app.models.video import Video
from app.schemas.note import NoteCreate
from app.schemas.note import NoteResponse
from app.schemas.note import NoteUpdate
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


def normalize_tag_string(raw_tags: Optional[str]) -> Optional[str]:
    """规范化标签字符串，统一去空、去重并按逗号存储。"""
    if raw_tags is None:
        return None

    normalized_tags = []
    seen = set()
    for part in str(raw_tags).split(","):
        tag = part.strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized_tags.append(tag)

    return ",".join(normalized_tags) if normalized_tags else None


def extract_keywords(text: str, top_k: int = 10) -> List[str]:
    """提取文本关键词"""
    if not text or len(text) < 10:
        return []
    keywords = jieba.analyse.extract_tags(text, topK=top_k)
    return keywords


def generate_content_vector(text: str, db: Session) -> str:
    """生成内容向量"""
    if not text or len(text.strip()) == 0:
        return json.dumps([0.0] * 10)

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        all_notes = db.query(Note).all()
        all_contents = [note.content for note in all_notes if note.content and len(note.content.strip()) > 0]

        if not all_contents:
            all_contents = ["默认文本示例", text]
        else:
            all_contents.append(text)

        tfidf_vectorizer = TfidfVectorizer(max_features=100, stop_words=None, min_df=1)
        tfidf_vectorizer.fit(all_contents)
        vector = tfidf_vectorizer.transform([text]).toarray()[0]
        return json.dumps(vector.tolist())
    except Exception as e:
        logger.error(f"生成内容向量时出错: {str(e)}")
        return json.dumps([0.0] * 10)


def _debug_note_payload(
    action: str,
    *,
    note_id: Optional[int] = None,
    video_id: Optional[int] = None,
    title: str = "",
    content: str = "",
    tags: Optional[str] = None,
):
    logger.debug(
        "note %s | note_id=%s | video_id=%s | title=%s | content_len=%s | tags=%s",
        action,
        note_id,
        video_id,
        title,
        len(str(content or "")),
        tags or "",
    )


@router.get("/notes")
async def get_notes(
    video_id: Optional[int] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    note_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取所有笔记"""
    query = db.query(Note)

    if video_id:
        query = query.filter(Note.video_id == video_id)
    if tag:
        query = query.filter(Note.tags.like(f"%{tag}%"))
    if note_type:
        query = query.filter(Note.note_type == note_type)
    if search:
        query = query.filter(
            or_(Note.title.like(f"%{search}%"), Note.content.like(f"%{search}%"), Note.keywords.like(f"%{search}%"))
        )

    notes = query.order_by(Note.updated_at.desc()).all()
    return {"status": "success", "data": [note.to_dict() for note in notes]}


@router.get("/notes/{note_id}")
async def get_note(note_id: int, db: Session = Depends(get_db)):
    """获取单个笔记"""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return {"status": "success", "data": note.to_dict()}


@router.post("/notes")
async def create_note(data: NoteCreate, db: Session = Depends(get_db)):
    """创建新笔记"""
    if not data.title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    logger.debug(
        "note create request | video_id=%s | title=%s | note_type=%s | timestamps=%s",
        data.video_id,
        data.title,
        data.note_type,
        len(data.timestamps or []),
    )

    if data.video_id:
        video = db.query(Video).filter(Video.id == data.video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

    content = data.content or ""
    keywords = extract_keywords(content)
    keywords_str = ",".join(keywords) if keywords else None
    content_vector = generate_content_vector(content, db)

    note = Note(
        title=data.title,
        content=content,
        content_vector=content_vector,
        note_type=data.note_type or "text",
        video_id=data.video_id,
        tags=normalize_tag_string(data.tags),
        keywords=keywords_str,
    )

    db.add(note)
    db.commit()
    db.refresh(note)
    _debug_note_payload(
        "created", note_id=note.id, video_id=note.video_id, title=note.title, content=note.content, tags=note.tags
    )

    # 添加时间戳
    if data.timestamps:
        for ts in data.timestamps:
            timestamp = NoteTimestamp(note_id=note.id, time_seconds=ts.time_seconds, subtitle_text=ts.subtitle_text)
            db.add(timestamp)
        db.commit()
        logger.debug("note timestamps attached | note_id=%s | count=%s", note.id, len(data.timestamps))

    return {"status": "success", "data": note.to_dict()}


@router.put("/notes/{note_id}")
async def update_note(note_id: int, data: NoteUpdate, db: Session = Depends(get_db)):
    """更新笔记"""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    logger.debug("note update request | note_id=%s | fields=%s", note_id, sorted(data.model_fields_set))

    updated_fields = data.model_fields_set

    if "title" in updated_fields:
        note.title = data.title
    if "content" in updated_fields:
        note.content = data.content or ""
        keywords = extract_keywords(note.content)
        note.keywords = ",".join(keywords) if keywords else None
        note.content_vector = generate_content_vector(note.content, db)
    if "note_type" in updated_fields:
        note.note_type = data.note_type
    if "video_id" in updated_fields:
        if data.video_id is None:
            note.video_id = None
        else:
            video = db.query(Video).filter(Video.id == data.video_id).first()
            if not video:
                raise HTTPException(status_code=404, detail="视频不存在")
            note.video_id = data.video_id
    if "tags" in updated_fields:
        note.tags = normalize_tag_string(data.tags)

    db.commit()
    db.refresh(note)
    _debug_note_payload(
        "updated", note_id=note.id, video_id=note.video_id, title=note.title, content=note.content, tags=note.tags
    )
    return {"status": "success", "data": note.to_dict()}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, db: Session = Depends(get_db)):
    """删除笔记"""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    logger.debug("note delete request | note_id=%s | video_id=%s | title=%s", note_id, note.video_id, note.title)

    db.delete(note)
    db.commit()
    return {"status": "success", "message": "笔记已删除"}


@router.post("/notes/{note_id}/timestamps")
async def add_timestamp(
    note_id: int, time_seconds: float, subtitle_text: Optional[str] = None, db: Session = Depends(get_db)
):
    """添加时间戳"""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    logger.debug(
        "note timestamp request | note_id=%s | time_seconds=%s | subtitle_len=%s",
        note_id,
        time_seconds,
        len(str(subtitle_text or "")),
    )

    timestamp = NoteTimestamp(note_id=note.id, time_seconds=time_seconds, subtitle_text=subtitle_text)
    db.add(timestamp)
    db.commit()
    db.refresh(timestamp)
    logger.debug("note timestamp persisted | note_id=%s | timestamp_id=%s", note_id, timestamp.id)

    return {"status": "success", "data": timestamp.to_dict()}


@router.delete("/notes/{note_id}/timestamps/{timestamp_id}")
async def delete_timestamp(note_id: int, timestamp_id: int, db: Session = Depends(get_db)):
    """删除时间戳"""
    timestamp = (
        db.query(NoteTimestamp).filter(NoteTimestamp.id == timestamp_id, NoteTimestamp.note_id == note_id).first()
    )
    if not timestamp:
        raise HTTPException(status_code=404, detail="时间戳不存在")

    db.delete(timestamp)
    db.commit()
    return {"status": "success", "message": "时间戳已删除"}


@router.get("/tags")
async def get_tags(db: Session = Depends(get_db)):
    """获取所有标签"""
    logger.debug("note tags query request")
    notes = db.query(Note).filter(Note.tags.isnot(None)).all()

    all_tags = {}
    for note in notes:
        if note.tags:
            for tag in note.tags.split(","):
                tag = tag.strip()
                if tag:
                    all_tags[tag] = all_tags.get(tag, 0) + 1

    sorted_tags = [
        {"name": tag, "count": count} for tag, count in sorted(all_tags.items(), key=lambda x: x[1], reverse=True)
    ]
    return {"status": "success", "data": sorted_tags}


@router.post("/notes/similar")
async def get_similar_notes(content: str, limit: int = 5, db: Session = Depends(get_db)):
    """获取相似笔记"""
    if not content or len(content) < 10:
        return {"status": "success", "data": []}
    logger.debug("note similar request | content_len=%s | limit=%s", len(content), limit)

    keywords = extract_keywords(content)
    logger.debug("note similar keywords | keywords=%s", keywords[:5] if keywords else [])
    similar_notes = []

    if keywords:
        conditions = []
        for word in keywords[:5]:
            conditions.append(
                or_(Note.title.like(f"%{word}%"), Note.content.like(f"%{word}%"), Note.keywords.like(f"%{word}%"))
            )

        keyword_matches = db.query(Note).filter(or_(*conditions)).all()
        similar_notes.extend(keyword_matches)

    # 去重并限制数量
    unique_notes = []
    note_ids = set()
    for note in similar_notes:
        if note.id not in note_ids and len(unique_notes) < limit:
            note_ids.add(note.id)
            unique_notes.append(note)
    logger.debug("note similar result | hits=%s", len(unique_notes))

    return {"status": "success", "data": [note.to_dict() for note in unique_notes]}


@router.get("")
async def get_notes_alias(
    video_id: Optional[int] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    note_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """兼容 REST 风格的笔记列表入口。"""
    return await get_notes(video_id=video_id, tag=tag, search=search, note_type=note_type, db=db)


@router.post("")
async def create_note_alias(data: NoteCreate, db: Session = Depends(get_db)):
    """兼容 REST 风格的创建笔记入口。"""
    return await create_note(data=data, db=db)


@router.get("/{note_id}")
async def get_note_alias(note_id: int, db: Session = Depends(get_db)):
    """兼容 REST 风格的读取笔记入口。"""
    return await get_note(note_id=note_id, db=db)


@router.put("/{note_id}")
async def update_note_alias(note_id: int, data: NoteUpdate, db: Session = Depends(get_db)):
    """兼容 REST 风格的更新笔记入口。"""
    return await update_note(note_id=note_id, data=data, db=db)


@router.delete("/{note_id}")
async def delete_note_alias(note_id: int, db: Session = Depends(get_db)):
    """兼容 REST 风格的删除笔记入口。"""
    return await delete_note(note_id=note_id, db=db)


@router.post("/notes/batch-delete")
async def batch_delete_notes(note_ids: List[int], db: Session = Depends(get_db)):
    """批量删除笔记"""
    if not note_ids:
        raise HTTPException(status_code=400, detail="未提供笔记ID")

    notes = db.query(Note).filter(Note.id.in_(note_ids)).all()
    for note in notes:
        db.delete(note)
    db.commit()

    return {"status": "success", "message": f"成功删除 {len(notes)} 条笔记", "deleted_count": len(notes)}


@router.post("/notes/batch-export")
async def batch_export_notes(note_ids: List[int], db: Session = Depends(get_db)):
    """批量导出笔记"""
    if not note_ids:
        raise HTTPException(status_code=400, detail="未提供笔记ID")

    notes = db.query(Note).filter(Note.id.in_(note_ids)).all()
    if not notes:
        raise HTTPException(status_code=404, detail="未找到指定的笔记")

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w") as zf:
        for note in notes:
            md_content = f"# {note.title}\n\n"
            md_content += f"创建时间: {note.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            md_content += f"更新时间: {note.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            if note.tags:
                md_content += f"标签: {note.tags}\n"
            md_content += "\n---\n\n"
            md_content += note.content or ""

            safe_title = "".join([c if c.isalnum() or c in [" ", "-", "_"] else "_" for c in note.title])
            zf.writestr(f"{safe_title}.md", md_content.encode("utf-8"))

    memory_file.seek(0)
    return StreamingResponse(
        memory_file,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=notes_export.zip"},
    )


@router.get("/notes/{note_id}/export")
async def export_single_note(note_id: int, db: Session = Depends(get_db)):
    """导出单个笔记"""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="未找到指定的笔记")

    md_content = f"# {note.title}\n\n"
    md_content += f"创建时间: {note.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
    md_content += f"更新时间: {note.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
    if note.tags:
        md_content += f"标签: {note.tags}\n"
    md_content += "\n---\n\n"
    md_content += note.content or ""

    safe_title = "".join([c if c.isalnum() or c in [" ", "-", "_"] else "_" for c in note.title])

    return Response(
        content=md_content.encode("utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'},
    )


@router.post("/tags/sync")
async def sync_tags(db: Session = Depends(get_db)):
    """同步标签数据，清除不存在于任何笔记中的标签"""
    try:
        logger.debug("note tags sync request")
        notes = db.query(Note).all()

        # 收集所有实际存在的标签
        valid_tags = set()
        for note in notes:
            if note.tags:
                tags = note.tags.split(",")
                for tag in tags:
                    if tag.strip():
                        valid_tags.add(tag.strip())

        # 更新所有笔记的标签，确保只包含有效标签
        for note in notes:
            if note.tags:
                tags = note.tags.split(",")
                filtered_tags = [tag for tag in tags if tag.strip() in valid_tags]
                note.tags = ",".join(filtered_tags) if filtered_tags else None

        db.commit()
        logger.debug("note tags sync completed | valid_tags=%s", len(valid_tags))
        return {"status": "success", "message": "标签数据已同步", "valid_tags": list(valid_tags)}
    except Exception as e:
        db.rollback()
        logger.error(f"同步标签失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"同步标签失败: {str(e)}")
