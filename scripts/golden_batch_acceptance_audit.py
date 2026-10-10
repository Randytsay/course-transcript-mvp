"""Read-only, fail-closed Golden completion audit.

This script does not modify the pipeline, jobs, QA statuses or Drive.
A prior upload record is NOT live proof of remote identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

DEFAULT_BATCH_ID = "batch-20261009-104458-752e60"
BUDGET_CAP = Decimal("10.00")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def audit_job(data_root: Path, job: sqlite3.Row) -> dict:
    directory = data_root / "jobs" / job["id"]
    chirp = load_json(directory / "chirp-completeness.json")
    qa = load_json(directory / "qa-report.json")
    content = load_json(directory / "content-qa.json")
    provenance = load_json(directory / "golden-review-provenance.json")
    delivery = load_json(directory / "drive-publish-state.json")
    reasons = []

    if job["status"] != "completed":
        reasons.append("official_job_not_completed")
    if chirp.get("status") != "PASS" or chirp.get("blockers"):
        reasons.append("chirp_completeness_not_pass")
    if content.get("status") != "PASS":
        reasons.append("content_qa_not_pass")
    if qa.get("status") != "PASS" or qa.get("errors") or qa.get("review_required"):
        reasons.append("golden_qa_not_pass")
    if qa.get("reconciled_review_items"):
        # Reconciliation strings do not prove acoustic content or approval.
        reasons.append("qa_contains_unverified_reconciliation")
    if not provenance.get("semantic_review_completed"):
        reasons.append("independent_semantic_review_missing")
    else:
        audit_name = provenance.get("audit_file")
        audit_file = directory / audit_name if isinstance(audit_name, str) else None
        audit = load_json(audit_file) if audit_file and audit_file.parent == directory else {}
        if not audit or not audit.get("source_audio_sha256") or not audit.get("independent_review_evidence"):
            reasons.append("semantic_review_claim_lacks_reproducible_evidence")
        if provenance.get("status") == "AUDIO_SEMANTIC_REVIEW_VERIFIED" and not audit.get("independent_review_evidence"):
            reasons.append("self_asserted_acoustic_verification")

    local_outputs = {
        "srt": directory / "subtitles-cleaned.srt",
        "txt": directory / "transcript-cleaned.txt",
    }
    remote = delivery.get("files") or {}
    if delivery.get("status") != "completed":
        reasons.append("drive_publish_not_completed")
    for extension, local in local_outputs.items():
        record = remote.get(extension) or {}
        if not local.is_file():
            reasons.append(f"{extension}_local_missing")
            continue
        local_sha = sha256(local)
        remote_sha = record.get("remote_sha256")
        if record.get("status") != "completed" or record.get("sha256_match") is not True:
            reasons.append(f"{extension}_remote_verification_missing")
        if remote_sha != local_sha:
            reasons.append(f"{extension}_remote_sha_not_equal_current")
        if not record.get("verified_at"):
            reasons.append(f"{extension}_remote_timestamp_missing")

    # A publish-state record alone cannot prove its own authenticity, and this
    # read-only tool never performs network calls. Require an independent proof
    # anchored to exact content hashes before promotion.
    remote_proof = load_json(directory / "golden-remote-checksum-proof.json")
    if remote_proof.get("method") != "independent_remote_readback" or not remote_proof.get("verified_at"):
        reasons.append("independent_drive_readback_evidence_missing")
    else:
        proofs = remote_proof.get("files") or {}
        for extension, local in local_outputs.items():
            if not local.exists() or (proofs.get(extension) or {}).get("remote_sha256") != sha256(local):
                reasons.append(f"{extension}_independent_remote_sha_mismatch")

    with sqlite3.connect(f"file:{data_root / 'course-transcript.db'}?mode=ro", uri=True) as connection:
        running = connection.execute(
            "SELECT count(*) FROM stage_runs WHERE job_id=? AND stage='golden_qa' AND status='running'",
            (job["id"],),
        ).fetchone()[0]
        completed = connection.execute(
            "SELECT count(*) FROM stage_runs WHERE job_id=? AND stage='golden_qa' AND status='completed'",
            (job["id"],),
        ).fetchone()[0]
    if running or not completed:
        reasons.append("formal_golden_qa_stage_not_closed")
    return {
        "job_id": job["id"],
        "source_name": job["source_name"],
        "status": job["status"],
        "chirp_status": chirp.get("status", "MISSING"),
        "golden_qa_status": qa.get("status", "MISSING"),
        "golden_qa_review_count": len(qa.get("review_required") or []),
        "declared_semantic_review_completed": provenance.get("semantic_review_completed") is True,
        "declared_drive_status": delivery.get("status", "MISSING"),
        "strict_complete": not reasons,
        "blockers": sorted(set(reasons)),
    }


def audit_batch(data_root: Path, batch_id: str = DEFAULT_BATCH_ID) -> dict:
    db = data_root / "course-transcript.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        batch = connection.execute(
            "SELECT * FROM batches WHERE id=?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise ValueError(f"Unknown batch: {batch_id}")
        jobs = connection.execute(
            "SELECT * FROM jobs WHERE batch_id=? ORDER BY queue_position", (batch_id,)
        ).fetchall()
        if not jobs:
            raise ValueError(f"Batch has no jobs: {batch_id}")
        try:
            actual = Decimal(str(batch["actual_cost_usd"]))
            reserved = Decimal(str(batch["reserved_cost_usd"]))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Invalid batch cost ledger") from exc
    reports = [audit_job(data_root, job) for job in jobs]
    return {
        "batch_id": batch_id,
        "official_batch_completed_count_not_trusted": batch["completed_count"],
        "strict_completed_count": sum(report["strict_complete"] for report in reports),
        "expected_count": len(reports),
        "actual_cost_usd": str(actual),
        "reserved_cost_usd": str(reserved),
        "total_committed_usd": str(actual + reserved),
        "user_budget_cap_usd": str(BUDGET_CAP),
        "budget_within_cap": (actual >= 0 and reserved >= 0 and actual + reserved <= BUDGET_CAP),
        "jobs": reports,
        "writes_performed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = audit_batch(args.data_root, args.batch_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.require_complete and (
        report["strict_completed_count"] != report["expected_count"]
        or not report["budget_within_cap"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())