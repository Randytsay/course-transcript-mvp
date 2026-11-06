"""Read-only, local-only comparison of Golden V2 Chirp Ledger snapshots.

No remote provider calls, Production writes, repairs, copy/restore, or credential reads.
Results are *local Ledger evidence*, not Google Cloud billing reconciliation.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import quote

_JOB = re.compile(r"v2p_[0-9a-f]{32}\Z")


class EvidenceUnavailable(Exception):
    """An evidence source cannot be examined safely and consistently."""


def _existing_db(path: Path) -> Path:
    path = path.absolute()
    if path.is_symlink() or not path.is_file():
        raise EvidenceUnavailable("ledger file missing or symbolic link")
    if path.stat().st_size == 0:
        raise EvidenceUnavailable("ledger file empty")
    # This read-only utility is intentionally restricted to DELETE-mode DBs.
    # A live WAL-mode DB needs a separate operator-approved snapshot workflow;
    # querying it may create/access -shm or require different lock handling.
    if Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
        raise EvidenceUnavailable("WAL/SHM sidecar detected; refuse live direct comparison")
    return path


def _connect(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    if mode.lower() != "delete":
        db.close()
        raise EvidenceUnavailable("journal mode is not DELETE; no live snapshot claimed")
    # One explicit read transaction spans *every* query for this database.
    # Different databases cannot share a single point-in-time transaction.
    db.execute("BEGIN")
    return db


def inspect_snapshot(path: Path, job_id: str, *, label: str) -> tuple[dict, set[str]]:
    if not _JOB.fullmatch(job_id):
        raise EvidenceUnavailable("invalid canonical Golden V2 job id")
    path = _existing_db(Path(path))
    db = _connect(path)
    try:
        job = db.execute(
            "SELECT id, source_sha, revision, status, approved_cents "
            "FROM v2_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None:
            raise EvidenceUnavailable("target job absent from snapshot: " + label)
        stages = list(db.execute(
            "SELECT name, state, actual_cents FROM v2_stages WHERE job_id = ?",
            (job_id,),
        ))
        operations = list(db.execute(
            "SELECT window_id, source_sha, operation_name, status, actual_cents "
            "FROM v2_chirp_operations WHERE job_id = ?", (job_id,),
        ))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()

    names = [str(r["operation_name"]) for r in operations if r["operation_name"]]
    operation_names = set(names)
    costs = [r["actual_cents"] for r in operations]
    known_costs = [int(c) for c in costs if isinstance(c, int)]
    stage_actual = next((r["actual_cents"] for r in stages
                         if r["name"] == "chirp_independent"), None)
    op_sources = [r["source_sha"] for r in operations]
    result = {
        "label": label,
        "schema": "golden-v2-chirp-ledger-local-snapshot-v1",
        "revision": job["revision"],
        "job_status": job["status"],
        "approved_cents": job["approved_cents"],
        "operation_count": len(operations),
        "operation_status_counts": dict(sorted(Counter(r["status"] for r in operations).items())),
        "operation_cost_known_cents": sum(known_costs),
        "operation_cost_unknown_count": len(costs) - len(known_costs),
        "chirp_stage_actual_cents": stage_actual,
        "stage_vs_operation_cost_match": (stage_actual == sum(known_costs)
                                          if stage_actual is not None and
                                          len(costs) == len(known_costs) else None),
        "operation_name_missing_count": len(operations) - len(names),
        "operation_name_duplicate_count": len(names) - len(operation_names),
        "window_id_duplicate_count": len(operations) - len({r["window_id"] for r in operations}),
        "operations_source_mismatch_count": sum(s != job["source_sha"] for s in op_sources),
        "source_sha_internal": job["source_sha"],
        "snapshot_transaction": "EXPLICIT_READ_TRANSACTION",
        "journal_mode": "delete",
        "external_provider_reconciled": False,
        "external_billing_reconciled": False,
    }
    return result, operation_names


def compare_snapshots(current_path: Path, previous_path: Path, job_id: str) -> dict:
    current, current_names = inspect_snapshot(current_path, job_id, label="current")
    previous, previous_names = inspect_snapshot(previous_path, job_id, label="previous")
    source_sha_match = current.pop("source_sha_internal") == previous.pop("source_sha_internal")
    overlap = len(current_names & previous_names)
    missing_names = current["operation_name_missing_count"] + previous["operation_name_missing_count"]
    duplicate_names = current["operation_name_duplicate_count"] + previous["operation_name_duplicate_count"]
    gaps = []
    if not source_sha_match:
        gaps.append("SOURCE_SHA_DIFFERS")
    if current["operation_count"] != previous["operation_count"]:
        gaps.append("OPERATION_ROW_COUNT_DIFFERS")
    if current["operation_cost_known_cents"] != previous["operation_cost_known_cents"]:
        gaps.append("OPERATION_LOCAL_COST_DIFFERS")
    if current_names != previous_names:
        gaps.append("NAMED_OPERATION_IDENTITIES_DIFFER")
    if missing_names or duplicate_names:
        gaps.append("UNRESOLVED_OR_DUPLICATE_OPERATION_NAMES")
    if current["operations_source_mismatch_count"] or previous["operations_source_mismatch_count"]:
        gaps.append("OPERATIONS_MISMATCH_OWN_SOURCE_SHA")
    if current["stage_vs_operation_cost_match"] is not True or previous["stage_vs_operation_cost_match"] is not True:
        gaps.append("CHIRP_STAGE_COST_UNVERIFIED_OR_DIFFERS")
    return {
        "schema": "golden-v2-chirp-ledger-snapshot-diff-v1",
        "job_id": job_id,
        "status": "LOCAL_EVIDENCE_DIFFERS" if gaps else "LOCAL_EVIDENCE_MATCHES_NOT_CLOUD_VERIFIED",
        "current": current,
        "previous": previous,
        "comparison": {
            "same_source_sha": source_sha_match,
            "named_operations_common": overlap,
            "named_only_current": len(current_names - previous_names),
            "named_only_previous": len(previous_names - current_names),
            "named_operation_coverage_complete": missing_names == 0 and duplicate_names == 0,
            "differences": gaps,
            "snapshots_share_one_transaction": False,
        },
        "external_gcp_lro_state": "UNKNOWN",
        "external_gcp_billing": "UNKNOWN",
        "production_mutation_authorized": False,
        "provider_resubmission_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    try:
        output = compare_snapshots(args.current, args.previous, args.job_id)
    except (EvidenceUnavailable, sqlite3.Error, OSError, ValueError) as exc:
        # Don't disclose paths, table contents, SQL or secrets in a public report.
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__,
                          "production_mutation_authorized": False}))
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
