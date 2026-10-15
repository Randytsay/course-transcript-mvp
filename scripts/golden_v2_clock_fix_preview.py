"""Read-only source-bound preview for quarantined Chirp local clock fixes.

No GCS access, cost, authorization, writes, or provider calls.
Outputs only counts and reason codes, never recognized speech text.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from app.pipeline.golden_v2_chirp_local_clock_fix import parse_local_clock_fix_candidate
from app.jobs.golden_v2_ledger import V2Conflict

_JOB = re.compile(r"v2p_[0-9a-f]{32}\Z")
_WINDOW = re.compile(r"chunk-[0-9]{4}\Z")


def inspect_quarantined(*, database: Path, artifact_root: Path,
                        job_id: str) -> dict:
    db = Path(database)
    root = Path(artifact_root)
    if (not db.is_absolute() or not root.is_absolute()
            or db.is_symlink() or root.is_symlink() or not db.is_file()
            or not root.is_dir() or db.resolve().parent != root.resolve()
            or not _JOB.fullmatch(job_id)):
        raise ValueError("existing source-scoped V2 ledger and valid job ID required")
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT window_id,status,operation_name,source_sha,result_sha,"
            "actual_cents,start_ms,end_ms FROM v2_chirp_operations "
            "WHERE job_id=? AND status='REVIEW_REQUIRED' ORDER BY window_id",
            (job_id,)
        ).fetchall()
        jobs = conn.execute("SELECT source_sha FROM v2_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    if jobs is None:
        raise ValueError("Golden V2 job missing")
    jobs_root = root / "jobs"
    job_root = jobs_root / job_id
    if (jobs_root.is_symlink() or job_root.is_symlink()
            or not job_root.is_dir() or job_root.resolve().parent != jobs_root.resolve()):
        raise ValueError("quarantined job artifact tree missing or symlinked")
    result = []
    for row in rows:
        wid = row["window_id"]
        if not _WINDOW.fullmatch(wid) or row["source_sha"] != jobs["source_sha"]:
            raise ValueError("quarantined window/source identity invalid")
        evidence_dir = job_root / "golden-v2" / "chirp-evidence" / wid
        raw = evidence_dir / "provider-raw.json"
        review = evidence_dir / "review-required.json"
        info = {"window_id":wid,"start_ms":row["start_ms"],"end_ms":row["end_ms"],
                "status":"EVIDENCE_HOLD","safe_to_apply":False}
        if (raw.is_symlink() or review.is_symlink() or not raw.is_file()
                or not review.is_file() or raw.stat().st_size > 32 * 1024 * 1024):
            info["reason"]="RAW_OR_REVIEW_MISSING_OR_UNSAFE"
        else:
            raw_bytes = raw.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            try:
                payload = json.loads(review.read_bytes())
                if (digest != row["result_sha"]
                        or payload.get("raw_sha") != digest
                        or payload.get("operation_name") != row["operation_name"]
                        or payload.get("job_id") != job_id
                        or payload.get("window_id") != wid
                        or payload.get("source_sha") != row["source_sha"]
                        or payload.get("actual_cents") != row["actual_cents"]):
                    info["reason"]="EVIDENCE_BINDING_MISMATCH"
                else:
                    candidate = parse_local_clock_fix_candidate(raw_bytes, dict(row))
                    info.update({"status":"SAFE_FOR_OPERATOR_REVIEW" if candidate["safe_to_apply"] else "TARGETED_EVIDENCE_REVIEW_REQUIRED",
                                 "safe_to_apply":candidate["safe_to_apply"],
                                 "word_count":candidate["total_entry_count"],
                                 "normalized_token_count":candidate["fixable_word_count"],
                                 "unresolved_span_count":candidate["unresolvable_word_count"],
                                 "unresolved_reason_counts":dict(sorted(Counter(
                                     item.get("reason", "UNCLASSIFIED")
                                     for item in candidate["unresolvable"]).items())),
                                 "reason":"VERIFIED_PROVIDER_BOUNDARIES" if candidate["safe_to_apply"] else "MISSING_VERIFIED_BOUNDARY"})
            except (ValueError, UnicodeError, V2Conflict, OSError):
                info["reason"]="UNVERIFIABLE_PROVIDER_WORD_TIMES"
        result.append(info)
    return {"schema":"golden-v2-chirp-clock-fix-preview-v1",
            "job_id":job_id,"reviewed_windows":len(result),
            "safe_windows":sum(1 for item in result if item["safe_to_apply"]),
            "requires_attention":sum(1 for item in result if not item["safe_to_apply"]),
            "results":result,"provider_calls_started":False,
            "ledger_modified":False,"drive_mutations":False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_quarantined(
        database=args.database, artifact_root=args.artifact_root,
        job_id=args.job_id), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
