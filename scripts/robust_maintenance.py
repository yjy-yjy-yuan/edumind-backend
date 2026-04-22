#!/usr/bin/env python3
"""Daily robustness maintenance: stale temp cleanup + leak process scan.

- Clean stale temp/cache files to prevent dirty filesystem growth.
- Scan stale ffmpeg/yt-dlp python workers and optionally kill.
"""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

DEFAULT_DIRS = [
    "uploads/audio_temp",
    "uploads/cache",
    "temp",
]


def cleanup_dirs(root, dirs, older_than_hours, dry_run):
    now = time.time()
    removed = []
    errors = []
    threshold = older_than_hours * 3600

    for rel in dirs:
        d = root / rel
        if not d.exists() or not d.is_dir():
            continue
        for p in d.rglob("*"):
            try:
                if not p.is_file():
                    continue
                age = now - p.stat().st_mtime
                if age < threshold:
                    continue
                removed.append(str(p))
                if not dry_run:
                    p.unlink()
            except Exception as exc:
                errors.append("%s: %s" % (p, exc))

    return {"removed_count": len(removed), "removed_files": removed, "errors": errors}


def scan_stale_processes(min_age_seconds):
    ps = subprocess.run(
        ["ps", "-eo", "pid,etimes,command"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    stale = []
    for line in ps.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, age_s, cmd = parts
        try:
            pid = int(pid_s)
            age = int(age_s)
        except ValueError:
            continue

        is_target = any(k in cmd for k in ["ffmpeg", "yt-dlp", "video_download", "video_processing"])
        if not is_target:
            continue
        if age < min_age_seconds:
            continue

        stale.append({"pid": pid, "age_seconds": age, "command": cmd})
    return stale


def kill_processes(items, dry_run):
    killed = []
    for item in items:
        pid = item["pid"]
        if not dry_run:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
        killed.append(item)
    return killed


def main():
    parser = argparse.ArgumentParser(description="EduMind backend robustness maintenance")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="backend root")
    parser.add_argument("--older-than-hours", type=int, default=24)
    parser.add_argument("--stale-proc-age", type=int, default=7200, help="seconds")
    parser.add_argument("--kill-stale-processes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()

    cleanup_report = cleanup_dirs(root, DEFAULT_DIRS, args.older_than_hours, args.dry_run)
    stale = scan_stale_processes(args.stale_proc_age)
    killed = kill_processes(stale, dry_run=(args.dry_run or not args.kill_stale_processes))

    report = {
        "root": str(root),
        "dry_run": args.dry_run,
        "cleanup": cleanup_report,
        "stale_processes_detected": len(stale),
        "stale_processes": stale,
        "killed_count": len(killed),
        "killed_processes": killed,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
