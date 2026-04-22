"""聊天系统路由 - FastAPI 版本"""

import logging

from app.schemas.chat import ChatRequest
from app.utils.qa_utils import QAConfigError
from app.utils.qa_utils import QAProviderError
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/completions")
async def chat_completions(request: ChatRequest):
    """聊天完成接口（流式/非流式）

    对话模式:
    - direct: 直接回答模式，优先使用通义千问，DeepSeek 兜底
    - deep_think: 深度思考模式，强制使用 deepseek-reasoner
    """
    try:
        if request.stream:

            async def generate():
                try:
                    from app.utils.chat_system import stream_chat

                    for chunk in stream_chat(
                        request.messages, mode=request.mode, provider=request.provider, model=request.model or ""
                    ):
                        yield chunk
                except Exception as e:
                    logger.error(f"聊天生成出错: {str(e)}")
                    yield f"聊天生成出错: {str(e)}"

            return StreamingResponse(generate(), media_type="text/plain")

        from app.utils.chat_system import get_chat_response

        result = get_chat_response(
            request.messages, mode=request.mode, provider=request.provider, model=request.model or ""
        )
        return {"message": "success", **result}

    except HTTPException:
        raise
    except QAConfigError as exc:
        logger.error("聊天配置错误: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except QAProviderError as exc:
        logger.error("聊天模型调用失败: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as e:
        logger.error(f"聊天请求处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/modes")
async def list_modes():
    """获取可用的对话模式列表"""
    return {
        "modes": [
            {
                "id": "direct",
                "name": "直接回答",
                "description": "快速响应，优先使用通义千问，DeepSeek 兜底",
                "primary_model": "qwen-plus",
                "fallback_model": "deepseek-chat",
            },
            {
                "id": "deep_think",
                "name": "深度思考",
                "description": "深度推理，强制使用 DeepSeek 思考模型",
                "primary_model": "deepseek-reasoner",
                "fallback_model": None,
            },
        ]
    }


@router.get("/models")
async def list_models():
    """获取可用的模型列表（向后兼容）"""
    return {
        "models": [
            {"id": "qwen-plus", "name": "通义千问 Plus", "type": "online"},
            {"id": "qwen-max", "name": "通义千问 Max", "type": "online"},
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "type": "online"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "type": "online"},
        ]
    }


# 聊天历史存储 (内存存储，生产环境建议使用 Redis)
_chat_history = {"free": [], "video": []}


@router.get("/history")
async def get_history(mode: str = "free"):
    """获取聊天历史记录"""
    try:
        history = _chat_history.get(mode, [])
        return {"history": history}
    except Exception as e:
        logger.error(f"获取历史记录出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取历史记录出错: {str(e)}")


@router.post("/clear")
async def clear_history(mode: str = "all"):
    """清空聊天历史记录"""
    try:
        if mode == "all":
            _chat_history["free"] = []
            _chat_history["video"] = []
        else:
            _chat_history[mode] = []
        return {"message": "历史记录已清空"}
    except Exception as e:
        logger.error(f"清空历史记录出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"清空历史记录出错: {str(e)}")
