"""最小质量报告：样本量、错误率、漂移摘要（MVP）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from typing import Optional

from app.compounding.quality import QualityStats


@dataclass
class CompoundingExportReport:
    """导出任务汇总，可写入 JSON 侧车文件。"""

    batch_id: str
    date_key: str
    sources: List[str]
    formats: List[str]
    output_paths: List[str]
    search_stats: QualityStats
    similarity_stats: QualityStats
    similarity_score_mean: Optional[float]
    similarity_score_std: Optional[float]
    drift_summary: str

    @property
    def total_exported_lines(self) -> int:
        return self.search_stats.total_rows + self.similarity_stats.total_rows

    @property
    def combined_error_rate(self) -> float:
        """缺字段 + 异常值占比（不含重复）。"""
        t = self.search_stats.total_rows + self.similarity_stats.total_rows
        if t == 0:
            return 0.0
        bad = (
            self.search_stats.missing_field_rows
            + self.search_stats.outlier_rows
            + self.similarity_stats.missing_field_rows
            + self.similarity_stats.outlier_rows
        )
        return bad / t

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "date_key": self.date_key,
            "sources": self.sources,
            "formats": self.formats,
            "output_paths": self.output_paths,
            "total_exported_lines": self.total_exported_lines,
            "combined_error_rate": round(self.combined_error_rate, 4),
            "search": {
                "total_rows": self.search_stats.total_rows,
                "accepted_rows": self.search_stats.accepted_rows,
                "missing_field_rows": self.search_stats.missing_field_rows,
                "duplicate_keys": self.search_stats.duplicate_keys,
                "outlier_rows": self.search_stats.outlier_rows,
            },
            "similarity": {
                "total_rows": self.similarity_stats.total_rows,
                "accepted_rows": self.similarity_stats.accepted_rows,
                "missing_field_rows": self.similarity_stats.missing_field_rows,
                "duplicate_keys": self.similarity_stats.duplicate_keys,
                "outlier_rows": self.similarity_stats.outlier_rows,
            },
            "similarity_score_mean": self.similarity_score_mean,
            "similarity_score_std": self.similarity_score_std,
            "drift_summary": self.drift_summary,
        }


def compute_score_moments(scores: List[float]) -> tuple[Optional[float], Optional[float]]:
    if len(scores) < 1:
        return None, None
    if len(scores) == 1:
        return scores[0], 0.0
    mean = sum(scores) / len(scores)
    var = sum((x - mean) ** 2 for x in scores) / (len(scores) - 1)
    return mean, var**0.5
