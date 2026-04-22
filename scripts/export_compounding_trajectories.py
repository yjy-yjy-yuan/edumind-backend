#!/usr/bin/env python3
"""
轨迹导出调度入口（离线脚本，不影响在线请求）。

幂等：默认若 report_{date}_{batch}.json 已存在则跳过，除非 --force。
重试：数据库连接失败时最多重试 --max-retries 次（指数退避）。

示例：
  python scripts/export_compounding_trajectories.py --date 2026-04-10 --batch-id nightly
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

# 项目根：backend_fastapi/scripts -> backend_fastapi
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compounding.export_service import export_compounding_day
from app.core.database import SessionLocal


def _parse_list(s: str) -> list:
    return [x.strip() for x in s.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export search/similarity trajectories for compounding MVP")
    parser.add_argument("--date", required=True, help="UTC 日期 YYYY-MM-DD")
    parser.add_argument("--batch-id", default="default", help="批次 ID，用于输出文件名与幂等")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("exports/compounding"),
        help="输出目录（相对 backend_fastapi cwd）",
    )
    parser.add_argument("--sources", default="search,similarity", help="逗号分隔: search,similarity")
    parser.add_argument("--formats", default="jsonl,csv", help="逗号分隔: jsonl,csv")
    parser.add_argument("--force", action="store_true", help="覆盖已存在报告")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    report_file = out / f"report_{args.date}_{args.batch_id}.json"
    if report_file.exists() and not args.force:
        print(f"SKIP: exists {report_file} (use --force to overwrite)")
        return 0

    sources = _parse_list(args.sources)
    formats = _parse_list(args.formats)
    last_err: Optional[Exception] = None
    for attempt in range(max(1, args.max_retries)):
        db = SessionLocal()
        try:
            export_compounding_day(
                db,
                date_key=args.date,
                batch_id=args.batch_id,
                output_dir=out,
                sources=sources,
                formats=formats,
            )
            print(f"OK: wrote under {out.resolve()}")
            return 0
        except Exception as exc:
            last_err = exc
            db.rollback()
            wait = min(2.0**attempt, 30.0)
            print(f"WARN: attempt {attempt + 1}/{args.max_retries} failed: {exc}; sleep {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
        finally:
            db.close()
    print(f"FAIL: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
