"""数据质量：缺字段、异常值、重复。"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List


@dataclass
class QualityStats:
    """单源导出累计质检统计。"""

    total_rows: int = 0
    missing_field_rows: int = 0
    duplicate_keys: int = 0
    outlier_rows: int = 0
    accepted_rows: int = 0


def record_dedupe_key(source: str, record_id: str) -> str:
    return f"{source}:{record_id}"


def validate_feedback_dict(row: Dict[str, Any]) -> List[str]:
    """返回质量标记列表（空表示无问题）。"""
    flags: List[str] = []
    if row.get("schema_version") != "compounding_feedback_v1":
        flags.append("schema_mismatch")
    if not row.get("source"):
        flags.append("missing_source")
    if not row.get("record_id"):
        flags.append("missing_record_id")
    if "trace_id" not in row:
        flags.append("missing_trace_id_field")
    if "features" not in row:
        flags.append("missing_features")
    meta = row.get("meta")
    if meta is None:
        flags.append("missing_meta")
    elif not isinstance(meta, dict):
        flags.append("invalid_meta")
    elif not meta.get("date_key"):
        flags.append("missing_meta_date_key")
    return flags


def outlier_checks_similarity(features: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    lat = features.get("latency_ms")
    if lat is not None and (lat < 0 or lat > 600_000):
        flags.append("outlier_latency_ms")
    score = features.get("score")
    if score is not None:
        try:
            s = float(score)
            if s < -0.01 or s > 1.01:
                flags.append("outlier_score")
        except (TypeError, ValueError):
            flags.append("invalid_score")
    return flags


def outlier_checks_search(features: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    ms = features.get("total_time_ms")
    if ms is not None and (int(ms) < 0 or int(ms) > 600_000):
        flags.append("outlier_total_time_ms")
    return flags
