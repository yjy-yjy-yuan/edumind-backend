"""Storage maintenance for runtime artifacts.

This module periodically cleans stale runtime files that are safe to regenerate:
- temp upload files
- audio temp/cache files
- partial download residue (.part/.ytdl/.tmp)
- stale chunking temp dirs (/tmp/edumind_chunks_*)
- old chroma backup dirs (data/chroma_broken_*)
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from threading import Event, Thread
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RELATIVE_DIRS: Tuple[str, ...] = (
    "temp",
    "uploads/audio_temp",
    "uploads/cache",
)
PARTIAL_DOWNLOAD_SUFFIXES: Tuple[str, ...] = (
    ".part",
    ".ytdl",
    ".tmp",
    ".temp",
    ".download",
)
TMP_CHUNK_DIR_PREFIX = "edumind_chunks_"
CHROMA_BROKEN_PREFIX = "chroma_broken_"


def _safe_unlink(path: Path) -> bool:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to remove file | path=%s | error=%s", path, exc)
    return False


def _safe_rmtree(path: Path) -> bool:
    try:
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to remove directory | path=%s | error=%s", path, exc)
    return False


def _is_older_than(path: Path, older_than_seconds: int, now_ts: float) -> bool:
    try:
        return (now_ts - path.stat().st_mtime) >= older_than_seconds
    except Exception:  # noqa: BLE001
        return False


def cleanup_stale_files(base_dir: Path, *, older_than_hours: int) -> Dict[str, int]:
    now_ts = time.time()
    threshold = max(1, int(older_than_hours * 3600))
    removed = 0
    scanned = 0

    for rel in DEFAULT_RELATIVE_DIRS:
        target = base_dir / rel
        if not target.exists() or not target.is_dir():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            scanned += 1
            if not _is_older_than(path, threshold, now_ts):
                continue
            if _safe_unlink(path):
                removed += 1

    return {"scanned": scanned, "removed": removed}


def cleanup_partial_download_residue(upload_dir: Path, *, older_than_hours: int) -> Dict[str, int]:
    now_ts = time.time()
    threshold = max(1, int(older_than_hours * 3600))
    removed = 0
    scanned = 0

    if not upload_dir.exists() or not upload_dir.is_dir():
        return {"scanned": 0, "removed": 0}

    for path in upload_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in PARTIAL_DOWNLOAD_SUFFIXES:
            continue
        scanned += 1
        if not _is_older_than(path, threshold, now_ts):
            continue
        if _safe_unlink(path):
            removed += 1

    return {"scanned": scanned, "removed": removed}


def cleanup_stale_chunk_dirs(*, older_than_hours: int) -> Dict[str, int]:
    now_ts = time.time()
    threshold = max(1, int(older_than_hours * 3600))
    tmp_root = Path("/tmp")
    removed = 0
    scanned = 0

    for path in tmp_root.glob(f"{TMP_CHUNK_DIR_PREFIX}*"):
        if not path.is_dir():
            continue
        scanned += 1
        if not _is_older_than(path, threshold, now_ts):
            continue
        if _safe_rmtree(path):
            removed += 1

    return {"scanned": scanned, "removed": removed}


def cleanup_chroma_backups(
    data_dir: Path,
    *,
    older_than_hours: int,
    keep_recent: int,
) -> Dict[str, int]:
    now_ts = time.time()
    threshold = max(1, int(older_than_hours * 3600))
    keep_count = max(0, int(keep_recent))
    removed = 0
    scanned = 0

    if not data_dir.exists() or not data_dir.is_dir():
        return {"scanned": 0, "removed": 0}

    candidates = [p for p in data_dir.glob(f"{CHROMA_BROKEN_PREFIX}*") if p.is_dir()]
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    protected = set(candidates[:keep_count])

    for path in candidates:
        scanned += 1
        if path in protected:
            continue
        if not _is_older_than(path, threshold, now_ts):
            continue
        if _safe_rmtree(path):
            removed += 1

    return {"scanned": scanned, "removed": removed}


def run_storage_maintenance_once(
    *,
    base_dir: Path,
    file_max_age_hours: int,
    chunk_dir_max_age_hours: int,
    chroma_backup_max_age_hours: int,
    keep_recent_chroma_backups: int,
) -> Dict[str, Dict[str, int]]:
    uploads_dir = base_dir / "uploads"
    data_dir = base_dir / "data"
    report = {
        "stale_runtime_files": cleanup_stale_files(base_dir, older_than_hours=file_max_age_hours),
        "partial_download_residue": cleanup_partial_download_residue(uploads_dir, older_than_hours=file_max_age_hours),
        "stale_chunk_dirs": cleanup_stale_chunk_dirs(older_than_hours=chunk_dir_max_age_hours),
        "chroma_backups": cleanup_chroma_backups(
            data_dir,
            older_than_hours=chroma_backup_max_age_hours,
            keep_recent=keep_recent_chroma_backups,
        ),
    }
    logger.info("Storage maintenance report | %s", report)
    return report


def _worker_loop(
    *,
    stop_event: Event,
    base_dir: Path,
    interval_seconds: int,
    file_max_age_hours: int,
    chunk_dir_max_age_hours: int,
    chroma_backup_max_age_hours: int,
    keep_recent_chroma_backups: int,
) -> None:
    sleep_seconds = max(60, int(interval_seconds))
    while not stop_event.is_set():
        try:
            run_storage_maintenance_once(
                base_dir=base_dir,
                file_max_age_hours=file_max_age_hours,
                chunk_dir_max_age_hours=chunk_dir_max_age_hours,
                chroma_backup_max_age_hours=chroma_backup_max_age_hours,
                keep_recent_chroma_backups=keep_recent_chroma_backups,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Storage maintenance iteration failed | error=%s", exc)
        stop_event.wait(sleep_seconds)


def start_storage_maintenance_worker(
    *,
    base_dir: Path,
    interval_seconds: int,
    file_max_age_hours: int,
    chunk_dir_max_age_hours: int,
    chroma_backup_max_age_hours: int,
    keep_recent_chroma_backups: int,
) -> Tuple[Thread, Event]:
    stop_event = Event()
    worker = Thread(
        target=_worker_loop,
        kwargs={
            "stop_event": stop_event,
            "base_dir": base_dir,
            "interval_seconds": interval_seconds,
            "file_max_age_hours": file_max_age_hours,
            "chunk_dir_max_age_hours": chunk_dir_max_age_hours,
            "chroma_backup_max_age_hours": chroma_backup_max_age_hours,
            "keep_recent_chroma_backups": keep_recent_chroma_backups,
        },
        daemon=True,
        name="storage-maintenance-worker",
    )
    worker.start()
    return worker, stop_event


def stop_storage_maintenance_worker(worker: Thread, stop_event: Event) -> None:
    stop_event.set()
    worker.join(timeout=5)
