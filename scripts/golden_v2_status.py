"""Read-only V2 task inspection for WebCodex/ChatGPT handoff.

Never starts a provider request, migrates DB, touches Drive, or exposes any
claim/approval token. Only authorized operator environments should run it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_read import get_v2_status


def read_v2_status(database: Path, *, job_id: str) -> dict[str, Any]:
    report = get_v2_status(database, job_id=job_id)
    if report is None:
        raise ValueError("unknown V2 job")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden V2 read-only job status")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    try:
        report = read_v2_status(args.database, job_id=args.job_id)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(f"cannot read V2 status: {exc}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
