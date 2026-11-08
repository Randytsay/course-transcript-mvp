"""No-network, read-only PR159 preflight for an existing Chirp ledger stage.

This is a diagnostic only. It never changes SQLite, creates approval claims,
calls a provider, declares acoustic PASS, or publishes to Google Drive. GCP
operations must still be verified independently before an operator settlement.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

_JOB_ID = re.compile(r"v2p_[0-9a-f]{32}\Z")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview_chirp_reconciliation(*, artifact_root: Path, job_id: str) -> dict[str, Any]:
    """Read exactly one pre-existing V2 job and independently check saved bytes."""
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ValueError("a canonical source-preflight V2 job id is required")
    root = Path(artifact_root)
    database = root / "ledger.sqlite"
    jobs = root / "jobs"
    job_dir = jobs / job_id
    if (root.is_symlink() or database.is_symlink() or jobs.is_symlink()
            or job_dir.is_symlink() or not database.is_file()
            or not job_dir.is_dir()
            or job_dir.resolve().parent != jobs.resolve()
            or database.resolve().parent != root.resolve()):
        raise ValueError("unsafe or missing isolated V2 ledger/job directory")

    blockers: list[str] = []
    with closing(sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro", uri=True,
    )) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        job = db.execute(
            "SELECT id,revision,source_sha,status,approved_cents FROM v2_jobs "
            "WHERE id=?", (job_id,),
        ).fetchone()
        if job is None:
            raise ValueError("unknown V2 job")
        stages = [dict(row) for row in db.execute(
            "SELECT name,state,estimate_cents,actual_cents,evidence_sha,"
            "review_required FROM v2_stages WHERE job_id=? ORDER BY ord",
            (job_id,),
        ).fetchall()]
        chirp = next((row for row in stages
                      if row["name"] == "chirp_independent"), None)
        if chirp is None:
            raise ValueError("missing isolated Chirp stage")
        if chirp["state"] not in {"RUNNING", "OVER_BUDGET", "REVIEW_REQUIRED"}:
            blockers.append("CHIRP_STAGE_NOT_A_RECONCILIATION_CANDIDATE")
        operations = [dict(row) for row in db.execute(
            "SELECT job_id,window_id,source_sha,clip_sha,operation_name,"
            "request_id,result_sha,status,actual_cents,start_ms,end_ms "
            "FROM v2_chirp_operations WHERE job_id=? "
            "ORDER BY start_ms,window_id", (job_id,),
        ).fetchall()]

    source = job_dir / "normalized.flac"
    source_match = (
        source.is_file() and not source.is_symlink()
        and source.resolve().parent == job_dir.resolve()
        and _sha(source) == job["source_sha"]
    )
    if not source_match:
        blockers.append("NORMALIZED_SOURCE_SHA_MISMATCH")

    details: list[dict[str, Any]] = []
    known_cents = 0
    all_done = bool(operations)
    for row in operations:
        status = row["status"]
        all_done = all_done and status == "DONE"
        if status not in {"DONE"}:
            blockers.append("UNRECONCILED_PROVIDER_OPERATION")
        cents = row["actual_cents"]
        if cents is None:
            blockers.append("OPERATION_ACTUAL_USAGE_UNKNOWN")
        else:
            known_cents += int(cents)
        evidence_root = job_dir / "golden-v2" / "chirp-evidence"
        window_id = row["window_id"]
        safe_window_id = (
            isinstance(window_id, str)
            and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", window_id) is not None
            and window_id not in {".", ".."}
        )
        evidence_dir = evidence_root / window_id if safe_window_id else evidence_root
        raw = evidence_dir / "provider-raw.json"
        receipt = evidence_dir / "window.json"
        verified = False
        if (safe_window_id and row["source_sha"] == job["source_sha"]
                and row["result_sha"] is not None
                and evidence_root.is_dir() and not evidence_root.is_symlink()
                and evidence_dir.is_dir() and not evidence_dir.is_symlink()
                and evidence_dir.resolve().parent == evidence_root.resolve()
                and raw.is_file() and receipt.is_file()
                and not raw.is_symlink() and not receipt.is_symlink()
                and raw.resolve().parent == evidence_dir.resolve()
                and receipt.resolve().parent == evidence_dir.resolve()):
            try:
                meta = json.loads(receipt.read_bytes())
                verified = (
                    _sha(raw) == row["result_sha"]
                    and meta.get("raw_sha") == row["result_sha"]
                    and meta.get("source_sha") == row["source_sha"]
                    and meta.get("clip_sha") == row["clip_sha"]
                    and meta.get("operation_name") == row["operation_name"]
                    and meta.get("request_id") == row["request_id"]
                    and meta.get("actual_cents") == row["actual_cents"]
                    and meta.get("start_ms") == row["start_ms"]
                    and meta.get("end_ms") == row["end_ms"]
                )
            except (ValueError, OSError, TypeError):
                verified = False
        if not verified:
            blockers.append("PROVIDER_RECEIPT_OR_SHA_MISMATCH")
        details.append({
            "window_id": row["window_id"],
            "status": status,
            "actual_cents": cents,
            "saved_provider_result_sha256": row["result_sha"],
            "receipt_sha_verified": verified,
        })
    if not operations:
        blockers.append("NO_PROVIDER_OPERATIONS")

    report_summaries: list[dict[str, Any]] = []
    report_dir = job_dir / "golden-v2"
    if report_dir.is_dir() and not report_dir.is_symlink():
        for path in sorted(report_dir.glob("chirp-acoustic-qa-*.json"))[:20]:
            if path.is_symlink() or path.resolve().parent != report_dir.resolve():
                continue
            try:
                obj = json.loads(path.read_bytes())
                merged_sha = obj.get("merged_sha256")
                candidate = report_dir / (
                    f"chirp-merged-candidate-{str(merged_sha)[:20]}.json")
                bound = (obj.get("source_sha256") == job["source_sha"]
                         and isinstance(merged_sha, str)
                         and bool(re.fullmatch(r"[0-9a-f]{64}", merged_sha))
                         and candidate.is_file() and not candidate.is_symlink()
                         and candidate.resolve().parent == report_dir.resolve()
                         and _sha(candidate) == merged_sha)
                report_summaries.append({
                    "report_sha256": _sha(path),
                    "acoustic_gate": obj.get("status"),
                    "source_and_candidate_bound": bound,
                    "gaps_examined": len(obj.get("gaps_examined", []))
                        if isinstance(obj.get("gaps_examined"), list) else None,
                    "repair_intervals": len(obj.get("repair_plan", {}).get("intervals", []))
                        if isinstance(obj.get("repair_plan"), dict)
                        and isinstance(obj["repair_plan"].get("intervals"), list)
                        else None,
                })
            except (ValueError, OSError, TypeError):
                blockers.append("ACOUSTIC_REPORT_UNREADABLE")
    review_ready = any(
        item["acoustic_gate"] == "REVIEW"
        and item["source_and_candidate_bound"]
        for item in report_summaries
    )
    if not review_ready:
        blockers.append("NO_VERIFIED_ACOUSTIC_REVIEW")

    other_actual = sum(int(s["actual_cents"] or 0) for s in stages
                       if s["name"] != "chirp_independent")
    within_approval = (
        type(job["approved_cents"]) is int
        and other_actual + known_cents <= job["approved_cents"]
    )
    if not within_approval:
        blockers.append("KNOWN_USAGE_EXCEEDS_JOB_APPROVAL")
    if chirp["actual_cents"] is not None and int(chirp["actual_cents"]) != known_cents:
        blockers.append("CHIRP_STAGE_AND_WINDOW_COST_DIFFER")

    return {
        "schema": "golden-v2-chirp-local-reconciliation-preview-v1",
        "job_id": job_id,
        "revision": job["revision"],
        "status": job["status"],
        "source_sha256": job["source_sha"],
        "normalized_flac_sha_verified": source_match,
        "chirp_stage_state": chirp["state"],
        "chirp_estimated_cents": chirp["estimate_cents"],
        "chirp_stage_actual_cents": chirp["actual_cents"],
        "chirp_window_known_actual_cents": known_cents,
        "other_stage_known_actual_cents": other_actual,
        "known_total_actual_cents": known_cents + other_actual,
        "approved_job_cents": job["approved_cents"],
        "operation_count": len(operations),
        "all_operations_saved_done": all_done,
        "operation_receipts": details,
        "acoustic_reports": report_summaries,
        "blockers": sorted(set(blockers)),
        "local_evidence_reconciled": not blockers,
        "live_gcp_provider_status_verified": False,
        "golden_qa_pass": False,
        "provider_calls_started": False,
        "drive_mutations_started": False,
        "ledger_modified": False,
        "read_only": True,
        "deployment_or_paid_authorization": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    print(json.dumps(preview_chirp_reconciliation(
        artifact_root=args.artifact_root, job_id=args.job_id),
        ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
