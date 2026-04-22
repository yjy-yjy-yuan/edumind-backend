"""统一遥测事件 schema 与校验。"""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from typing import Dict
from typing import Optional

SCHEMA_VERSION = 1

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$", re.I)
_MODULE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$", re.I)


class AnalyticsStatus(str, Enum):
    """统一状态（跨 search / similarity 等模块）。"""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    DEGRADED = "degraded"
    STARTED = "started"


@dataclass
class AnalyticsEvent:
    """
    统一分析管道事件。

    字段约定：
    - event_type: 业务事件名（如 search_completed、similarity_call_success）
    - trace_id: 跨模块关联 ID
    - module: 逻辑模块（search、similarity、…）
    - latency_ms: 可选耗时（毫秒）
    - status: AnalyticsStatus 值
    - metadata: 其余结构化负载（不得存放不可序列化对象）
    """

    event_type: str
    trace_id: str
    module: str
    status: str
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_envelope(self) -> Dict[str, Any]:
        """输出带时间戳与 schema 版本的外层结构（便于下游 ETL）。"""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "schema_version": SCHEMA_VERSION,
            "event_type": self.event_type,
            "trace_id": self.trace_id,
            "module": self.module,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


def validate_analytics_event(event: AnalyticsEvent) -> None:
    """校验事件字段；失败抛出 ValueError。"""
    if not event.event_type or not _EVENT_TYPE_RE.match(event.event_type):
        raise ValueError(f"invalid event_type: {event.event_type!r}")
    if not event.trace_id or len(event.trace_id) > 128:
        raise ValueError("invalid trace_id")
    if not event.module or not _MODULE_RE.match(event.module):
        raise ValueError(f"invalid module: {event.module!r}")
    allowed = {s.value for s in AnalyticsStatus}
    if event.status not in allowed:
        raise ValueError(f"invalid status: {event.status!r}, expected one of {sorted(allowed)}")
    if event.latency_ms is not None:
        if not math.isfinite(event.latency_ms):
            raise ValueError("latency_ms must be finite")
        if event.latency_ms < 0 or event.latency_ms > 1e12:
            raise ValueError("latency_ms out of range")
