"""反馈与训练侧输入格式（MVP，不含训练平台本体）。"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List

# 与导出 JSONL 行对齐的版本号
FEEDBACK_SCHEMA_VERSION = "compounding_feedback_v1"


@dataclass
class FeedbackRecordV1:
    """
    单条可复用样本（JSONL 一行）。

    字段说明：
    - source: search | similarity
    - record_id: 来源表主键或稳定哈希
    - trace_id: 追踪 ID（若有）
    - features: 模型可读特征（已脱敏）
    - labels: 可训练标签占位（MVP 多为空或弱标签）
    - quality_flags: 质检命中项
    """

    source: str
    record_id: str
    trace_id: str
    features: Dict[str, Any] = field(default_factory=dict)
    labels: Dict[str, Any] = field(default_factory=dict)
    quality_flags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "source": self.source,
            "record_id": self.record_id,
            "trace_id": self.trace_id,
            "features": dict(self.features),
            "labels": dict(self.labels),
            "quality_flags": list(self.quality_flags),
            "meta": dict(self.meta),
        }
