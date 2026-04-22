"""轨迹导出：按日、JSONL/CSV，不写在线热路径。"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import List
from typing import Optional
from typing import Set

from app.compounding.formats import FEEDBACK_SCHEMA_VERSION
from app.compounding.formats import FeedbackRecordV1
from app.compounding.quality import QualityStats
from app.compounding.quality import outlier_checks_search
from app.compounding.quality import outlier_checks_similarity
from app.compounding.quality import record_dedupe_key
from app.compounding.quality import validate_feedback_dict
from app.compounding.report import CompoundingExportReport
from app.compounding.report import compute_score_moments
from app.compounding.sanitization import SanitizerConfig
from app.compounding.sanitization import default_sanitizer_config
from app.compounding.sanitization import sanitize_search_features
from app.compounding.sanitization import sanitize_similarity_features
from app.models.semantic_search_log import SemanticSearchLog
from app.models.similarity_audit_log import SimilarityAuditLogModel
from app.repositories.similarity_audit_log_repository import SimilarityAuditLogRepository
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _utc_day_bounds(date_key: str) -> tuple[datetime, datetime]:
    """
    基于 UTC 的 date_key 生成日界。

    注意：当前模型的 created_at 为 timezone-naive；这里按“UTC 语义的 naive 时间”查询，
    以避免 DB 时区与应用时区混用导致的边界漂移。
    """
    d = datetime.strptime(date_key, "%Y-%m-%d").date()
    start_utc = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    start = start_utc.replace(tzinfo=None)
    end = end_utc.replace(tzinfo=None)
    return start, end


def fetch_search_logs_for_date(db: Session, date_key: str) -> List[SemanticSearchLog]:
    start, end = _utc_day_bounds(date_key)
    return (
        db.query(SemanticSearchLog)
        .filter(SemanticSearchLog.created_at >= start, SemanticSearchLog.created_at < end)
        .order_by(SemanticSearchLog.id.asc())
        .all()
    )


def fetch_similarity_logs_for_date(db: Session, date_key: str) -> List[SimilarityAuditLogModel]:
    repo = SimilarityAuditLogRepository()
    return repo.get_logs_by_date(date_key, db)


def _accumulate_stats(stats: QualityStats, flags: List[str], dedupe_key: str, seen: Set[str]) -> None:
    is_dup = dedupe_key in seen
    if is_dup:
        stats.duplicate_keys += 1
        flags.append("duplicate_record")
    else:
        seen.add(dedupe_key)
    if any(f.startswith("missing_") for f in flags) or "schema_mismatch" in flags:
        stats.missing_field_rows += 1
    if any(f.startswith("outlier_") or f == "invalid_score" for f in flags):
        stats.outlier_rows += 1
    if not is_dup and not any(f.startswith("missing_") for f in flags) and "schema_mismatch" not in flags:
        stats.accepted_rows += 1


def export_compounding_day(
    db: Session,
    date_key: str,
    batch_id: str,
    output_dir: Path,
    sources: List[str],
    formats: List[str],
    sanitizer: Optional[SanitizerConfig] = None,
) -> CompoundingExportReport:
    """
    导出单日轨迹到 output_dir，文件名含 batch_id 与 date_key，便于幂等。

    sources: search, similarity 的子集
    formats: jsonl, csv 的子集
    """
    sanitizer = sanitizer or default_sanitizer_config()
    output_dir.mkdir(parents=True, exist_ok=True)
    search_stats = QualityStats()
    similarity_stats = QualityStats()
    seen_keys: Set[str] = set()
    output_paths: List[str] = []
    scores: List[float] = []

    if "search" in sources and "jsonl" in formats:
        path = output_dir / f"search_{date_key}_{batch_id}.jsonl"
        with path.open("w", encoding="utf-8") as fp:
            for row in fetch_search_logs_for_date(db, date_key):
                search_stats.total_rows += 1
                feats = sanitize_search_features(row, sanitizer)
                trace_id = str(getattr(row, "trace_id", "") or "")
                rec = FeedbackRecordV1(
                    source="search",
                    record_id=str(row.id),
                    trace_id=trace_id,
                    features=feats,
                    labels={},
                    quality_flags=[],
                    meta={
                        "date_key": date_key,
                        "trace_id": trace_id,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    },
                )
                flags = list(validate_feedback_dict(rec.to_dict()))
                flags.extend(outlier_checks_search(feats))
                key = record_dedupe_key("search", str(row.id))
                _accumulate_stats(search_stats, flags, key, seen_keys)
                rec.quality_flags = flags
                fp.write(json.dumps(rec.to_dict(), ensure_ascii=False, default=str) + "\n")
        output_paths.append(str(path))

    if "similarity" in sources and "jsonl" in formats:
        path = output_dir / f"similarity_{date_key}_{batch_id}.jsonl"
        with path.open("w", encoding="utf-8") as fp:
            for row in fetch_similarity_logs_for_date(db, date_key):
                similarity_stats.total_rows += 1
                feats = sanitize_similarity_features(row, sanitizer)
                if feats.get("score") is not None:
                    try:
                        scores.append(float(feats["score"]))
                    except (TypeError, ValueError):
                        pass
                tid = getattr(row, "trace_id", "") or ""
                rec = FeedbackRecordV1(
                    source="similarity",
                    record_id=str(row.id),
                    trace_id=tid,
                    features=feats,
                    labels={"score": feats.get("score")} if feats.get("score") is not None else {},
                    quality_flags=[],
                    meta={
                        "date_key": date_key,
                        "trace_id": row.trace_id,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    },
                )
                flags = list(validate_feedback_dict(rec.to_dict()))
                flags.extend(outlier_checks_similarity(feats))
                key = record_dedupe_key("similarity", str(row.id))
                _accumulate_stats(similarity_stats, flags, key, seen_keys)
                rec.quality_flags = flags
                fp.write(json.dumps(rec.to_dict(), ensure_ascii=False, default=str) + "\n")
        output_paths.append(str(path))

    if "search" in sources and "csv" in formats:
        path = output_dir / f"search_{date_key}_{batch_id}.csv"
        rows = fetch_search_logs_for_date(db, date_key)
        if rows:
            with path.open("w", encoding="utf-8", newline="") as fp:
                w = csv.DictWriter(
                    fp,
                    fieldnames=["record_id", "user_id_hash", "query_text", "result_count", "total_time_ms", "date_key"],
                )
                w.writeheader()
                for row in rows:
                    feats = sanitize_search_features(row, sanitizer)
                    w.writerow(
                        {
                            "record_id": row.id,
                            "user_id_hash": feats.get("user_id_hash"),
                            "query_text": feats.get("query_text"),
                            "result_count": feats.get("result_count"),
                            "total_time_ms": feats.get("total_time_ms"),
                            "date_key": date_key,
                        }
                    )
        else:
            path.write_text("", encoding="utf-8")
        output_paths.append(str(path))

    mean, std = compute_score_moments(scores)
    drift = ""
    if mean is not None:
        drift = (
            f"similarity_score_mean={mean:.4f} std={std or 0:.4f} (batch {date_key}); " "full drift model not in MVP"
        )

    report = CompoundingExportReport(
        batch_id=batch_id,
        date_key=date_key,
        sources=list(sources),
        formats=list(formats),
        output_paths=output_paths,
        search_stats=search_stats,
        similarity_stats=similarity_stats,
        similarity_score_mean=mean,
        similarity_score_std=std,
        drift_summary=drift,
    )
    report_path = output_dir / f"report_{date_key}_{batch_id}.json"
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    output_paths.append(str(report_path))
    logger.info(
        "compounding export wrote search=%s similarity=%s",
        search_stats.total_rows,
        similarity_stats.total_rows,
    )
    return report
