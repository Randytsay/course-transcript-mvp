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


def read_v2_status(database: Path, *, job_id: str) -> dict[str, Any]:
    if not database.is_file() or not job_id.startswith("v2_"):
        raise ValueError("existing V2 database and job id required")
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        job = db.execute(
            "SELECT id,source_sha,mode,status,revision,estimated_cents,"
            "approved_cents,created_at FROM v2_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if job is None:
            raise ValueError("unknown V2 job")
        stages = db.execute(
            "SELECT ord,name,state,estimate_cents,actual_cents,evidence_sha,"
            "review_required,updated_at FROM v2_stages WHERE job_id=? ORDER BY ord",
            (job_id,),
        ).fetchall()
        return {
            "job": dict(job),
            "stages": [dict(row) for row in stages],
            "actual_cents": sum(int(row["actual_cents"] or 0) for row in stages),
            "provider_calls_started": "UNKNOWN_WITHOUT_PROVIDER_MANIFEST",
            "golden_qa_complete": next(
                (row["state"] == "COMPLETE" for row in stages if row["name"] == "golden_qa"),
                False,
            ),
            "production_deployed": "UNVERIFIED",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden V2 read-only job status")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    try:
        report = read_v2_status(args.database, job_id=args.job_id)
    except (ValueError, sqlite3.Error) as exc:
        parser.error(f"cannot read V2 status: {exc}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
