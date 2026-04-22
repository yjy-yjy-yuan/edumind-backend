"""问答系统路由 - FastAPI 版本"""

import json
import logging
from typing import Optional

from app.core.database import SessionLocal
from app.core.database import get_db
from app.models.qa import Question
from app.models.video import Video
from app.schemas.qa import AskRequest
from app.utils.qa_utils import SUPPORTED_QA_PROVIDERS
from app.utils.qa_utils import QAConfigError
from app.utils.qa_utils import QAProviderError
from app.utils.qa_utils import QASystem
from app.utils.qa_utils import resolve_provider_label
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_QA_MODES = {"video", "free"}


def serialize_stream_event(event: dict) -> str:
    return f"{json.dumps(event, ensure_ascii=False)}\n"


def validate_provider(provider: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in SUPPORTED_QA_PROVIDERS:
        raise HTTPException(status_code=400, detail="provider 仅支持 qwen 或 deepseek，且不能为空")
    return normalized_provider


def resolve_qa_routing(request: AskRequest) -> tuple[str, str, bool]:
    """解析问答路由：将 chat_mode 转换为 provider 和 deep_thinking。

    chat_mode 优先级高于 provider + deep_thinking。
    - direct: 优先通义千问（DeepSeek 兜底由后端处理）
    - deep_think: 强制 DeepSeek reasoner

    Returns:
        (provider, model, deep_thinking)
    """
    chat_mode = getattr(request, "chat_mode", None)
    if chat_mode == "deep_think":
        return "deepseek", request.model or "", True
    if chat_mode == "direct":
        return "qwen", request.model or "", False
    # 向后兼容：使用 provider + deep_thinking
    return request.provider, request.model or "", request.deep_thinking


def _debug_qa_context(
    prefix: str, *, video: Optional[Video], request: AskRequest, normalized_mode: str, normalized_provider: str
):
    logger.debug(
        "%s | mode=%s | provider=%s | video_id=%s | question_len=%s | history_messages=%s | subtitle_count=%s | has_summary=%s",
        prefix,
        normalized_mode,
        normalized_provider,
        request.video_id,
        len(str(request.question or "")),
        len(request.history or []),
        len(getattr(video, "subtitles", []) or []) if video is not None else 0,
        bool(getattr(video, "summary", "") or ""),
    )


def persist_question_record(
    db: Session,
    *,
    video_id: Optional[int],
    content: str,
    answer: Optional[str],
) -> dict:
    question = Question(
        video_id=video_id,
        content=content,
        answer=answer,
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    logger.debug(
        "qa question persisted | question_id=%s | video_id=%s | answer_len=%s",
        question.id,
        video_id,
        len(str(answer or "")),
    )
    return question.to_dict()


@router.post("/ask")
async def ask_question(request: AskRequest, db: Session = Depends(get_db)):
    """提问并获取答案"""
    try:
        normalized_mode = str(request.mode or "").strip().lower() or "video"
        if normalized_mode not in SUPPORTED_QA_MODES:
            raise HTTPException(status_code=400, detail="不支持的问答模式，仅支持 video 或 free")
        normalized_provider, request_model, deep_thinking = resolve_qa_routing(request)
        validate_provider(normalized_provider)

        video = None

        # 视频问答模式需要验证视频
        if normalized_mode == "video":
            if not request.video_id:
                raise HTTPException(status_code=400, detail="视频问答模式需要提供视频ID")

            video = db.query(Video).filter(Video.id == request.video_id).first()
            if not video:
                raise HTTPException(status_code=404, detail="视频不存在")
        qa_system = QASystem(video=video)
        _debug_qa_context(
            "qa request accepted",
            video=video,
            request=request,
            normalized_mode=normalized_mode,
            normalized_provider=normalized_provider,
        )
        if normalized_mode == "video" and not qa_system.has_context():
            raise HTTPException(status_code=400, detail="该视频暂无可用于问答的字幕或摘要内容")

        effective_history = request.history

        # 流式响应
        if request.stream:

            def generate():
                stream_db = SessionLocal()
                try:
                    stream_video = None
                    if normalized_mode == "video":
                        stream_video = stream_db.query(Video).filter(Video.id == request.video_id).first()
                        if not stream_video:
                            yield serialize_stream_event(
                                {
                                    "type": "error",
                                    "stage": "validation",
                                    "message": "视频不存在",
                                    "detail": "视频不存在",
                                    "progress": 100,
                                }
                            )
                            return

                    stream_qa_system = QASystem(video=stream_video)
                    logger.debug(
                        "qa stream context ready | video_id=%s | subtitle_count=%s | has_summary=%s",
                        request.video_id,
                        len(getattr(stream_video, "subtitles", []) or []),
                        bool(getattr(stream_video, "summary", "") or ""),
                    )
                    if normalized_mode == "video" and not stream_qa_system.has_context():
                        yield serialize_stream_event(
                            {
                                "type": "error",
                                "stage": "validation",
                                "message": "该视频暂无可用于问答的字幕或摘要内容",
                                "detail": "该视频暂无可用于问答的字幕或摘要内容",
                                "progress": 100,
                            }
                        )
                        return

                    yield serialize_stream_event(
                        {
                            "type": "status",
                            "stage": "accepted",
                            "message": "问题已提交，等待处理",
                            "progress": 5,
                            "provider": normalized_provider,
                            "provider_label": resolve_provider_label(normalized_provider),
                            "chat_mode": getattr(request, "chat_mode", None),
                        }
                    )

                    stream_history = request.history
                    logger.debug(
                        "qa stream start | history_messages=%s | provider=%s | model=%s",
                        len(stream_history or []),
                        normalized_provider,
                        request.model or "",
                    )

                    for event in stream_qa_system.answer_stream(
                        request.question,
                        provider=normalized_provider,
                        model=request_model,
                        deep_thinking=deep_thinking,
                        mode=normalized_mode,
                        history=stream_history,
                    ):
                        if event.get("type") == "answer":
                            logger.debug(
                                "qa stream answer event | video_id=%s | answer_len=%s | references=%s",
                                request.video_id,
                                len(str(event.get("answer") or "")),
                                len(event.get("references") or []),
                            )
                            stored_question = persist_question_record(
                                stream_db,
                                video_id=request.video_id if normalized_mode == "video" else None,
                                content=request.question,
                                answer=event.get("answer"),
                            )
                            event.update(
                                {
                                    **stored_question,
                                    "provider": normalized_provider,
                                    "mode": normalized_mode,
                                    "model": event.get("model"),
                                }
                            )
                        logger.info(
                            "QA stream event | type=%s | stage=%s | provider=%s | model=%s | video_id=%s",
                            event.get("type"),
                            event.get("stage"),
                            event.get("provider"),
                            event.get("model"),
                            request.video_id,
                        )
                        yield serialize_stream_event(event)
                except QAConfigError as exc:
                    logger.error("问答流配置错误 | error=%s", exc)
                    stream_db.rollback()
                    yield serialize_stream_event(
                        {
                            "type": "error",
                            "stage": "config_error",
                            "message": str(exc),
                            "detail": str(exc),
                            "progress": 100,
                        }
                    )
                except QAProviderError as exc:
                    logger.error("问答流模型调用失败 | error=%s", exc)
                    stream_db.rollback()
                    yield serialize_stream_event(
                        {
                            "type": "error",
                            "stage": "provider_error",
                            "message": "模型调用失败",
                            "detail": str(exc),
                            "progress": 100,
                        }
                    )
                except Exception as exc:
                    logger.error("问答流处理失败 | error=%s", exc)
                    stream_db.rollback()
                    yield serialize_stream_event(
                        {
                            "type": "error",
                            "stage": "server_error",
                            "message": "问答处理失败，请稍后重试",
                            "detail": "问答处理失败，请稍后重试",
                            "progress": 100,
                        }
                    )
                finally:
                    stream_db.close()

            return StreamingResponse(generate(), media_type="application/x-ndjson; charset=utf-8")

        result = qa_system.ask(
            request.question,
            provider=normalized_provider,
            model=request_model,
            deep_thinking=deep_thinking,
            mode=normalized_mode,
            history=effective_history,
        )
        logger.debug(
            "qa answer completed | video_id=%s | provider=%s | model=%s | answer_len=%s | references=%s",
            request.video_id,
            result.get("provider"),
            result.get("model"),
            len(str(result.get("answer") or "")),
            len(result.get("references") or []),
        )
        stored_question = persist_question_record(
            db,
            video_id=request.video_id if normalized_mode == "video" else None,
            content=request.question,
            answer=result["answer"],
        )

        response_payload = stored_question
        response_payload.update(
            {
                "user_id": request.user_id,
                "provider": result["provider"],
                "mode": normalized_mode,
                "provider_label": result["provider_label"],
                "model": result["model"],
                "references": result["references"],
            }
        )
        return response_payload

    except HTTPException:
        raise
    except QAConfigError as exc:
        logger.error("问答配置错误 | error=%s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except QAProviderError as exc:
        logger.error("问答模型调用失败 | error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.error("处理问题时出错 | error=%s", exc)
        raise HTTPException(status_code=500, detail="问答处理失败，请稍后重试")


@router.get("/history/{video_id}")
async def get_qa_history(
    video_id: int,
    provider: str = Query(..., description="模型提供方: qwen, deepseek"),
    user_id: Optional[int] = Query(default=None, description="当前用户ID，用于隔离问答空间"),
    mode: str = Query(default="video", description="问答模式，视频问答固定为 video"),
):
    """获取视频的问答历史"""
    try:
        normalized_mode = str(mode or "").strip().lower() or "video"
        if normalized_mode not in SUPPORTED_QA_MODES:
            raise HTTPException(status_code=400, detail="不支持的问答模式，仅支持 video 或 free")

        validate_provider(provider)
        return {
            "message": "当前 questions 表未按 provider 隔离，已禁用服务端历史恢复；请以前端本地缓存为准。",
            "total": 0,
            "questions": [],
            "messages": [],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("获取问答历史时出错 | error=%s", exc)
        raise HTTPException(status_code=500, detail="获取问答历史失败，请稍后重试")
