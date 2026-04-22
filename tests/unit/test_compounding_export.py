"""Compounding 轨迹导出与质检（SQLite 内存库）。"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.compounding.export_service import export_compounding_day
from app.compounding.quality import validate_feedback_dict
from app.compounding.sanitization import SanitizerConfig
from app.compounding.sanitization import default_sanitizer_config
from app.compounding.sanitization import sanitize_search_features
from app.core.config import settings
from app.models.base import Base
from app.models.semantic_search_log import SemanticSearchLog
from app.models.similarity_audit_log import SimilarityAuditLogModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def compounding_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


class TestCompoundingExport:
    def test_export_jsonl_and_report(self, compounding_db: Session, tmp_path: Path):
        day = "2026-04-10"
        created = datetime(2026, 4, 10, 8, 0, 0)
        slog = SemanticSearchLog(
            user_id=42,
            query_text="hello world",
            is_global=True,
            video_ids_searched=[1, 2],
            result_count=3,
            total_time_ms=150,
            limit_used=20,
            threshold_used=Decimal("0.550"),
            created_at=created,
        )
        compounding_db.add(slog)
        sim = SimilarityAuditLogModel(
            trace_id="t1",
            date_key=day,
            tag1="a",
            tag2="b",
            event_type="similarity_call_success",
            provider="openai",
            model="m",
            prompt_version="v2",
            success=True,
            score=0.8,
            latency_ms=10.0,
            provider_latency_ms=5.0,
            parse_latency_ms=5.0,
            parse_ok=True,
            retry_count=0,
            retry_failed=False,
            created_at=created,
        )
        compounding_db.add(sim)
        compounding_db.commit()

        out = tmp_path / "out"
        report = export_compounding_day(
            compounding_db,
            date_key=day,
            batch_id="t1",
            output_dir=out,
            sources=["search", "similarity"],
            formats=["jsonl", "csv"],
        )
        assert report.search_stats.total_rows == 1
        assert report.similarity_stats.total_rows == 1
        assert report.combined_error_rate == 0.0
        assert report.similarity_score_mean is not None

        rep_file = out / f"report_{day}_t1.json"
        assert rep_file.exists()
        summary = json.loads(rep_file.read_text(encoding="utf-8"))
        assert summary["batch_id"] == "t1"
        assert "drift_summary" in summary

        lines = (out / f"search_{day}_t1.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["schema_version"] == "compounding_feedback_v1"
        assert row["source"] == "search"
        assert "trace_id" in row
        assert "user_id_hash" in row["features"]
        assert "trace_id" in row["features"]
        assert "date_key" in row["meta"]
        assert row["features"]["user_id_hash"] != "42"

    def test_sanitize_truncation(self):
        cfg = SanitizerConfig(query_text_max_chars=5)
        r = SimpleNamespace(
            query_text="123456789",
            user_id=1,
            is_global=True,
            result_count=0,
            total_time_ms=1,
            limit_used=1,
            threshold_used=0.5,
        )
        f = sanitize_search_features(r, cfg)
        assert f["query_text"] == "12345"
        assert f["query_text_truncated"] is True

    def test_default_sanitizer_reads_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "COMPOUNDING_USER_ID_HASH_SALT", "unit_test_salt")
        monkeypatch.setattr(settings, "COMPOUNDING_QUERY_TEXT_MAX_CHARS", 11)
        cfg = default_sanitizer_config()
        assert cfg.user_id_hash_salt == "unit_test_salt"
        assert cfg.query_text_max_chars == 11

    def test_validate_feedback(self):
        assert (
            validate_feedback_dict(
                {
                    "schema_version": "compounding_feedback_v1",
                    "source": "x",
                    "record_id": "1",
                    "trace_id": "",
                    "features": {},
                    "meta": {"date_key": "2026-04-10"},
                }
            )
            == []
        )
        bad = validate_feedback_dict(
            {
                "schema_version": "wrong",
                "source": "x",
                "record_id": "1",
                "features": {},
                "trace_id": "",
                "meta": {},
            }
        )
        assert "schema_mismatch" in bad
        assert "missing_meta_date_key" in bad
