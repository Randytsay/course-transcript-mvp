"""Persistent, provider-free Golden V2 worker pass."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs.golden_v2_read import list_v2_active_jobs
from app.pipeline.golden_v2_coordinator import run_ready_local
from app.pipeline.golden_v2_paid_readiness import inspect_paid_readiness

_JOB_ID = re.compile(r"v2(?:p)?_[0-9a-f]{32}\Z")


def run_v2_worker_pass(
    *, database: Path, artifact_root: Path, worker_id: str = "golden-v2-worker",
    max_jobs: int = 100,
) -> dict[str, Any]:
    """Advance ready local stages in active jobs, returning explicit blockers.

    The worker never constructs a provider client, performs network IO, approves
    costs, or writes Drive/GCS. Provider and human-review stages remain queued
    for a separately authorized executor or reviewer.
    """
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker identity required")
    if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
        raise ValueError("worker job bound must be 1..1000")
    database = Path(database).expanduser()
    artifact_root = Path(artifact_root).expanduser()
    if (artifact_root.is_symlink() or database.is_symlink()
            or not artifact_root.is_dir()
            or database.resolve().parent != artifact_root.resolve()):
        return {"action": "LEDGER_PATH_REJECTED", "jobs_seen": 0, "outcomes": []}
    root = artifact_root.resolve()
    if not database.is_file():
        return {"action": "LEDGER_NOT_READY", "jobs_seen": 0, "outcomes": []}
    try:
        ledger = GoldenV2Ledger.open_existing(database)
        jobs = list_v2_active_jobs(database, limit=max_jobs)
    except (OSError, sqlite3.Error) as exc:
        return {"action": "LEDGER_UNAVAILABLE", "reason": str(exc),
                "jobs_seen": 0, "outcomes": []}

    outcomes: list[dict[str, Any]] = []
    for job in jobs:
        job_id = job["job_id"]
        if not _JOB_ID.fullmatch(job_id):
            outcomes.append({"job_id": job_id, "action": "INVALID_JOB_ID"})
            continue
        jobs_root = root / "jobs"
        candidate = jobs_root / job_id
        if candidate.is_symlink() or not candidate.is_dir():
            outcomes.append({"job_id": job_id, "action": "JOB_ARTIFACT_DIRECTORY_MISSING"})
            continue
        resolved = candidate.resolve(strict=True)
        if resolved.parent != jobs_root.resolve():
            outcomes.append({"job_id": job_id, "action": "JOB_ARTIFACT_PATH_REJECTED"})
            continue
        try:
            local_outcomes = run_ready_local(
                ledger=ledger, job_id=job_id, job_dir=resolved,
                worker_id=worker_id,
            )
            item: dict[str, Any] = {"job_id": job_id, "actions": local_outcomes}
            if local_outcomes and local_outcomes[-1]["action"] == "PROVIDER_STAGE_REQUIRED":
                readiness = inspect_paid_readiness(
                    database=database, artifact_root=root, job_id=job_id,
                )
                item["paid_readiness"] = readiness
            outcomes.append(item)
        except Exception as exc:
            outcomes.append({"job_id": job_id, "action": "JOB_REQUIRES_REVIEW",
                             "reason": str(exc)})
    return {
        "action": "WORKER_CYCLE_COMPLETE",
        "worker_id": worker_id,
        "jobs_seen": len(jobs),
        "outcomes": outcomes,
        "provider_calls_started": False,
        "drive_mutation_started": False,
        "gcs_mutation_started": False,
    }
