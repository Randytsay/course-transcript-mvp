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
    job_data = dict(job)
    job_data["prior_approved_source_count"] = db.execute(
        "SELECT COUNT(*) FROM v2_jobs WHERE source_sha=? AND id<>? "
        "AND approved_cents IS NOT NULL",
        (job["source_sha"], job["id"]),
    ).fetchone()[0]
    return {
        "job": job_data, "stages": stages,
        "actual_cents": sum(int(stage["actual_cents"] or 0) for stage in stages),
        "provider_calls_started": "UNKNOWN_WITHOUT_PROVIDER_MANIFEST",
        "golden_qa_complete": any(stage["name"] == "golden_qa" and
                                  stage["state"] == "COMPLETE" for stage in stages),
        "production_deployed": "UNVERIFIED",
    }


def get_v2_status(database: Path, *, job_id: str) -> dict[str, Any] | None:
    if not ((job_id.startswith("v2_") and len(job_id) == 35)
            or (job_id.startswith("v2p_") and len(job_id) == 36)):
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


def list_v2_active_jobs(database: Path, *, limit: int = 100) -> list[dict[str, str]]:
    """Read active job identities for local workers without migrating SQLite."""
    if not 1 <= limit <= 1000:
        raise ValueError("invalid active-job query limit")
    with closing(_connection(database)) as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT id,status FROM v2_jobs WHERE status IN ('QUEUED','PROCESSING') "
            "ORDER BY created_at,id LIMIT ?", (limit,),
        ).fetchall()
        return [{"job_id": str(row["id"]), "status": str(row["status"])}
                for row in rows]


def list_v2_rescue_jobs(database: Path, *, limit: int = 100) -> list[dict[str, str]]:
    """Only jobs with a held Gemini primary and an eligible independent Chirp.

    Include a saved RUNNING Chirp regardless of the primary's held state.
    Never discover unrelated REVIEW_REQUIRED jobs for provider dispatch.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("invalid rescue-job query limit")
    with closing(_connection(database)) as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT j.id, j.status FROM v2_jobs j "
            "JOIN v2_stages p ON p.job_id=j.id AND p.name='gemini_primary' "
            "JOIN v2_stages c ON c.job_id=j.id AND c.name='chirp_independent' "
            "WHERE j.approved_cents IS NOT NULL AND p.state='REVIEW_REQUIRED' "
            "AND ((j.status='NEEDS_REVIEW' AND c.state='PENDING') "
            "OR (j.status='PROCESSING' AND c.state='RUNNING')) "
            "ORDER BY j.created_at,j.id LIMIT ?", (limit,)
        ).fetchall()
        return [{"job_id": str(row["id"]), "status": str(row["status"])}
                for row in rows]
