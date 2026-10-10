"""Guarded one-step bridge for the nine-course Golden batch.

Runs only a reviewed, free, reversible punctuation repair.  Everything else
stays in REVIEW/HOLD; a VAD or local structural QA PASS alone cannot publish.

Intended to be invoked from the existing golden-nine-triage.timer, never from
a second scheduler. Host Python needs only the standard library: the actual
QA/rerun occurs inside the production worker container where app dependencies
already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

BATCH_ID = "batch-20261009-104458-752e60"
CAP = Decimal("10.00")
CONTAINER = "course-transcript-source-pipeline-worker-1"
CONTAINER_REPAIR = "/app/data/golden-runner/golden_punctuation_revision.py"
CONTAINER_QA = "/app/app/providers/qa_report.py"
PUNCTUATION_ISSUE = re.compile(r"^subtitle contains repeated punctuation: seg-[0-9]+$")


def load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan(data_root: Path) -> dict:
    """Read only; never infer acoustic approval from a QA reconciliation."""
    db_path = data_root / "course-transcript.db"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        batch = con.execute(
            "SELECT id, actual_cost_usd, reserved_cost_usd, status, completed_count FROM batches WHERE id=?",
            (BATCH_ID,),
        ).fetchone()
        if batch is None:
            raise ValueError("Golden batch does not exist")
        try:
            actual = Decimal(str(batch["actual_cost_usd"]))
            reserved = Decimal(str(batch["reserved_cost_usd"]))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Invalid cost ledger; fail closed") from exc
        jobs = list(con.execute(
            "SELECT id, status, active_stage, locked_by, lease_expires_at, source_name "
            "FROM jobs WHERE batch_id=? ORDER BY queue_position", (BATCH_ID,)
        ))
    finally:
        con.close()
    if len(jobs) != 9:
        raise ValueError("Expected nine jobs; refusing unsafely scoped work")
    # No paid calls are issued even when ledger is below cap, but fail closed
    # on surprising/unreconciled budget figures.
    budget_safe = actual >= 0 and reserved >= 0 and actual + reserved <= CAP
    report = {
        "batch_id": BATCH_ID, "budget_safe": budget_safe,
        "actual_usd": str(actual), "reserved_usd": str(reserved),
        "claimed_batch_completed_count": batch["completed_count"],
        "eligible": [], "hold": [], "automatic_paid_calls": 0,
    }
    for job in jobs:
        jid = job["id"]
        directory = data_root / "jobs" / jid
        qa = load(directory / "qa-report.json")
        chirp = load(directory / "chirp-completeness.json")
        prov = load(directory / "golden-review-provenance.json")
        drive = load(directory / "drive-publish-state.json")
        issues = qa.get("review_required") or []
        blocked = []
        if job["status"] != "awaiting_review":
            blocked.append("job_not_awaiting_review")
        if job["active_stage"] != "golden_qa":
            blocked.append("not_in_golden_qa")
        if job["locked_by"] or job["lease_expires_at"]:
            blocked.append("worker_lease_present")
        if chirp.get("status") != "PASS" or chirp.get("blockers"):
            blocked.append("chirp_not_pass")
        if qa.get("status") != "REVIEW" or not isinstance(issues, list):
            blocked.append("golden_qa_not_review")
        if prov.get("semantic_review_completed") is True:
            blocked.append("semantic_review_already_attested")
        if drive.get("status") == "completed":
            blocked.append("drive_already_published")
        punctuation = [x for x in issues if isinstance(x, str) and PUNCTUATION_ISSUE.fullmatch(x)]
        if not punctuation:
            blocked.append("no_reviewed_punctuation_candidate")
        if not (directory / "subtitles-cleaned.json").is_file():
            blocked.append("no_canonical_subtitles")
        if not budget_safe:
            blocked.append("budget_over_cap_or_invalid")
        if blocked:
            report["hold"].append({"job_id": jid, "blockers": blocked,
                                   "golden_review_count": len(issues)})
        else:
            report["eligible"].append({
                "job_id": jid, "candidate_count": len(punctuation),
                "source_sha256": sha256(directory / "subtitles-cleaned.json"),
                "golden_review_count": len(issues),
            })
    report["eligible"].sort(key=lambda j: (-j["candidate_count"], j["job_id"]))
    return report


def worker_command(job_id: str, source_sha: str, apply: bool) -> list[str]:
    command = [
        "docker", "exec", "-i", CONTAINER, "python", CONTAINER_REPAIR,
        "--data-root", "/app/data", "--job-id", job_id,
        "--qa-script", CONTAINER_QA,
    ]
    if apply:
        command += ["--apply", "--expected-source-sha256", source_sha]
    return command


def one_step(data_root: Path, *, execute: bool, subprocess_run=subprocess.run) -> dict:
    state = scan(data_root)
    if not state["eligible"]:
        return {"outcome": "NO_SAFE_AUTOMATIC_ACTION", "eligible": 0,
                "hold_count": len(state["hold"]), "paid_calls": 0, "drive_writes": 0}
    entry = state["eligible"][0]
    jid = entry["job_id"]
    command = worker_command(jid, entry["source_sha256"], apply=False)
    preview = subprocess_run(command, capture_output=True, text=True, timeout=80)
    if preview.returncode != 0:
        return {"outcome": "PREVIEW_BLOCKED", "job_id": jid,
                "reason": preview.stderr[-400:], "paid_calls": 0}
    try:
        payload = json.loads(preview.stdout)
    except ValueError:
        return {"outcome": "PREVIEW_INVALID", "job_id": jid, "paid_calls": 0}
    changed_ids = [change.get("segment_id") for change in payload.get("changes", [])]
    if payload.get("source_sha256") != entry["source_sha256"] or not changed_ids:
        return {"outcome": "PREVIEW_NO_SAFE_CHANGE", "job_id": jid, "paid_calls": 0}
    if len(changed_ids) != entry["candidate_count"]:
        return {"outcome": "PREVIEW_MISMATCH_ISSUE_COUNT", "job_id": jid, "paid_calls": 0}
    if not execute:
        return {"outcome": "PREVIEW_ONLY", "job_id": jid, "change_count": len(changed_ids),
                "paid_calls": 0, "drive_writes": 0}
    # A source-locked, backup-first operation in the worker namespace. The
    # worker checks DB lease + runner lock again and rolls back if QA isn't
    # improved by precisely the expected punctuation warnings.
    action = subprocess_run(worker_command(jid, entry["source_sha256"], apply=True),
                            capture_output=True, text=True, timeout=150)
    if action.returncode:
        return {"outcome": "APPLY_FAILED_AND_POTENTIALLY_ROLLED_BACK",
                "job_id": jid, "reason": action.stderr[-600:], "paid_calls": 0}
    try:
        result = json.loads(action.stdout)
    except ValueError:
        return {"outcome": "APPLY_OUTPUT_INVALID_VERIFY_JOB_BEFORE_RETRY",
                "job_id": jid, "paid_calls": 0}
    if not result.get("changed") or result.get("paid_calls") != 0 or result.get("drive_uploads") != 0:
        return {"outcome": "APPLY_UNEXPECTED_CHECK_JOB", "job_id": jid, "paid_calls": 0}
    new_state = scan(data_root)
    return {"outcome": "QA_WARNING_REDUCED_NOT_GOLDEN_COMPLETE",
            "job_id": jid, "old_reviews": result.get("old_reviews"),
            "new_reviews": result.get("new_reviews"),
            "backup": result.get("backup"), "formal_complete": False,
            "remaining_eligible": len(new_state["eligible"]),
            "paid_calls": 0, "drive_writes": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "preview", "step"))
    parser.add_argument("--data-root", type=Path, default=Path(
        os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/opt/course-transcript-source/data")
    ))
    args = parser.parse_args()
    if args.command == "status":
        result = scan(args.data_root)
    else:
        result = one_step(args.data_root, execute=args.command == "step")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not str(result.get("outcome", "")).startswith(("APPLY_FAILED", "APPLY_OUTPUT_INVALID")) else 2


if __name__ == "__main__":
    raise SystemExit(main())