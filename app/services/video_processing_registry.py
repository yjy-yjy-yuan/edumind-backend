"""视频处理请求上下文缓存。

在不调整数据库表结构的前提下，记录当前视频最近一次提交处理时选择的模型和语言，
便于状态接口和页面回显“本次实际请求使用了什么模型”。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import Lock
from typing import Optional

_REGISTRY_LOCK = Lock()
_PROCESSING_REQUESTS: dict[int, dict] = {}


def remember_video_processing_request(
    video_id: int,
    *,
    model: str,
    language: str,
    source: str = "",
) -> dict:
    snapshot = {
        "requested_model": str(model or "").strip(),
        "requested_language": str(language or "").strip(),
        "effective_model": str(model or "").strip(),
        "source": str(source or "").strip(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    with _REGISTRY_LOCK:
        _PROCESSING_REQUESTS[int(video_id)] = snapshot
        return deepcopy(snapshot)


def get_video_processing_request(video_id: int) -> Optional[dict]:
    with _REGISTRY_LOCK:
        payload = _PROCESSING_REQUESTS.get(int(video_id))
        return deepcopy(payload) if payload else None


def forget_video_processing_request(video_id: int):
    with _REGISTRY_LOCK:
        _PROCESSING_REQUESTS.pop(int(video_id), None)
