"""Read-only Golden V2 ledger snapshots for owner tools.

This module intentionally does not initialize or migrate SQLite, resume jobs,
or expose claims and approval references.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def _connection(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError("Golden V2 ledger does not exist")
    db = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _snapshot(db: sqlite3.Connection, job: sqlite3.Row) -> dict[str, Any]:
    stages = [dict(row) for row in db.execute(
        "SELECT ord,name,state,estimate_cents,actual_cents,evidence_sha,"
        "review_required,updated_at FROM v2_stages WHERE job_id=? ORDER BY ord",
        (job["id"],),
    )]
    return {
        "job": dict(job), "stages": stages,
        "actual_cents": sum(int(stage["actual_cents"] or 0) for stage in stages),
        "provider_calls_started": "UNKNOWN_WITHOUT_PROVIDER_MANIFEST",
        "golden_qa_complete": any(stage["name"] == "golden_qa" and
                                  stage["state"] == "COMPLETE" for stage in stages),
        "production_deployed": "UNVERIFIED",
    }


def get_v2_status(database: Path, *, job_id: str) -> dict[str, Any] | None:
    if not job_id.startswith("v2_") or len(job_id) != 35:
        return None
    with closing(_connection(database)) as db:
        db.execute("BEGIN")
        job = db.execute(
            "SELECT id,source_sha,mode,status,revision,estimated_cents,"
            "approved_cents,created_at FROM v2_jobs WHERE id=?", (job_id,),
        ).fetchone()
        return _snapshot(db, job) if job is not None else None


def list_v2_status(database: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("invalid query limit")
    with closing(_connection(database)) as db:
        db.execute("BEGIN")
        jobs = db.execute(
            "SELECT id,source_sha,mode,status,revision,estimated_cents,"
            "approved_cents,created_at FROM v2_jobs "
            "ORDER BY created_at DESC,id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [_snapshot(db, job) for job in jobs]
