"""Golden V2 local SQLite snapshot reconciliation -- read-only, no provider calls.

Compares named existing SQLite files for ONE source-bound job using separate,
explicit, read-only transactions. Never creates a backup, opens a writer,
modifies a file, invokes GCP/Drive, or makes a cloud billing assertion.
Do not post source filenames, operation names or raw SQL rows to public issues.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import quote

JOB_RE = re.compile(r"v2p?_[0-9a-f]{32}\Z")
LABEL_RE = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
OPS_COLUMNS = {
    "job_id", "window_id", "source_sha", "operation_name", "status",
    "actual_cents", "estimated_cents",
}
STAGE_COLUMNS = {"job_id", "name", "state", "actual_cents"}


def _readonly_connection(path: Path) -> sqlite3.Connection:
    """Fail closed; never use immutable=1 against a possibly live DB."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be an existing regular SQLite file")
    uri = "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=8, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("SQLite query_only guard unavailable")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # Table names supplied exclusively by this module.
    return {str(r["name"]) for r in conn.execute("PRAGMA table_info(" + table + ")")}


def inspect_snapshot(path: Path, job_id: str) -> dict:
    """One SQLite read transaction for *all* table reads within this file."""
    if not JOB_RE.fullmatch(job_id):
        raise ValueError("invalid V2 job ID")
    db = _readonly_connection(path)
    try:
        db.execute("BEGIN")
        try:
            required = {"id", "source_sha", "status", "revision"}
            if (not required.issubset(_columns(db, "v2_jobs"))
                    or not OPS_COLUMNS.issubset(_columns(db, "v2_chirp_operations"))
                    or not STAGE_COLUMNS.issubset(_columns(db, "v2_stages"))):
                return {"evidence_state": "SCHEMA_INCOMPATIBLE", "op_names": set()}
            job = db.execute(
                "SELECT id,source_sha,status,revision FROM v2_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if job is None:
                return {"evidence_state": "JOB_NOT_PRESENT", "op_names": set()}
            operations = db.execute(
                "SELECT window_id,source_sha,operation_name,status,"
                "actual_cents,estimated_cents FROM v2_chirp_operations "
                "WHERE job_id=? ORDER BY window_id",
                (job_id,),
            ).fetchall()
            stage = db.execute(
                "SELECT name,state,actual_cents FROM v2_stages "
                "WHERE job_id=? AND name='chirp_independent'",
                (job_id,),
            ).fetchone()
            names = [r["operation_name"] for r in operations
                     if isinstance(r["operation_name"], str) and r["operation_name"].strip()]
            status_counts = dict(sorted(Counter(str(r["status"]) for r in operations).items()))
            source_mismatches = sum(r["source_sha"] != job["source_sha"] for r in operations)
            incomplete_cost = sum(r["actual_cents"] is None for r in operations)
            bad_cost = sum(
                r["actual_cents"] is not None
                and (type(r["actual_cents"]) is not int or r["actual_cents"] < 0)
                for r in operations
            )
            known_cents = sum(
                r["actual_cents"] for r in operations
                if type(r["actual_cents"]) is int and r["actual_cents"] >= 0
            )
            result = {
                "evidence_state": "READ",
                "source_sha": job["source_sha"],
                "job_status": job["status"],
                "job_revision": job["revision"],
                "operation_rows": len(operations),
                "operation_status_counts": status_counts,
                "operation_names_missing": len(operations) - len(names),
                "operation_names_duplicated": len(names) - len(set(names)),
                "window_ids_duplicated": len(operations)
                    - len({r["window_id"] for r in operations}),
                "source_sha_mismatch_rows": source_mismatches,
                "known_actual_cents": known_cents,
                "actual_cents_unknown_rows": incomplete_cost,
                "actual_cents_invalid_rows": bad_cost,
                "chirp_stage_state": stage["state"] if stage is not None else "MISSING",
                "chirp_stage_actual_cents": stage["actual_cents"] if stage else None,
                # Internal only: never serialize provider names or window IDs.
                "op_names": set(names),
            }
            return result
        finally:
            db.execute("ROLLBACK")
    finally:
        db.close()


def compare_snapshots(*, job_id: str, sources: list[tuple[str, Path]],
                      historical_claim: tuple[int, int] | None = None) -> dict:
    """Compare local evidence; there is no automatic external-billing PASS."""
    if not JOB_RE.fullmatch(job_id):
        raise ValueError("invalid V2 job ID")
    if not sources or sources[0][0] != "current":
        raise ValueError("first source must be named current")
    if len({label for label, _ in sources}) != len(sources):
        raise ValueError("source labels must be unique")
    snapshots = []
    internal = []
    ephemeral_key = secrets.token_bytes(32)
    for label, path in sources:
        if not LABEL_RE.fullmatch(label):
            raise ValueError("unsafe snapshot label")
        entry = inspect_snapshot(Path(path), job_id)
        names = entry.pop("op_names")
        if names:
            entry["operation_set_fingerprint"] = hmac.new(
                ephemeral_key,
                "\n".join(sorted(names)).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        else:
            entry["operation_set_fingerprint"] = None
        snapshots.append({"label": label, **entry})
        internal.append(names)

    differences = []
    current = snapshots[0]
    current_names = internal[0]
    for index in range(1, len(snapshots)):
        older = snapshots[index]
        older_names = internal[index]
        complete_names = (
            current["evidence_state"] == older["evidence_state"] == "READ"
            and current["operation_names_missing"] == 0
            and older["operation_names_missing"] == 0
            and current["operation_names_duplicated"] == 0
            and older["operation_names_duplicated"] == 0
        )
        differences.append({
            "historical_label": older["label"],
            "source_sha_same": (
                current.get("source_sha") == older.get("source_sha")
                if current.get("source_sha") and older.get("source_sha") else None
            ),
            "current_rows_minus_historical": (
                current["operation_rows"] - older["operation_rows"]
                if current["evidence_state"] == older["evidence_state"] == "READ" else None
            ),
            "current_minus_historical_operation_names": (
                len(current_names - older_names) if complete_names else None
            ),
            "historical_minus_current_operation_names": (
                len(older_names - current_names) if complete_names else None
            ),
            "common_operation_names": (
                len(current_names & older_names) if complete_names else None
            ),
            "identifiers_comparable": complete_names,
            "cost_delta_known_cents": (
                current["known_actual_cents"] - older["known_actual_cents"]
                if current["evidence_state"] == older["evidence_state"] == "READ"
                and current["actual_cents_unknown_rows"] == 0
                and older["actual_cents_unknown_rows"] == 0
                and current["actual_cents_invalid_rows"] == 0
                and older["actual_cents_invalid_rows"] == 0 else None
            ),
        })

    claim = None
    if historical_claim is not None:
        count, cents = historical_claim
        if count < 0 or cents < 0:
            raise ValueError("invalid historical claim")
        claim = {
            "provenance": "human-entered historical report; NOT GCP-verified",
            "reported_operation_count": count,
            "reported_cents": cents,
            "current_count_difference": (
                current["operation_rows"] - count
                if current["evidence_state"] == "READ" else None
            ),
            "current_known_cents_difference": (
                current["known_actual_cents"] - cents
                if current["evidence_state"] == "READ"
                and current["actual_cents_unknown_rows"] == 0
                and current["actual_cents_invalid_rows"] == 0 else None
            ),
        }

    # Never advertise safe replay, job completion, external billing
    # reconciliation or release GO, even when local snapshots match.
    return {
        "schema": "golden-v2-ledger-snapshot-reconcile-v1",
        "job_id": job_id,
        "snapshot_isolation": "separate_explicit_read_transactions_per_file",
        "cross_file_atomic": False,
        "snapshots": snapshots,
        "historical_comparisons": differences,
        "historical_claim_comparison": claim,
        "gcp_billing": "UNKNOWN",
        "external_provider_operations": "NOT_QUERIED",
        "missing_external_operations": "UNKNOWN",
        "reconciliation_complete": False,
        "golden_qa_pass": False,
        "paid_replay_authorized": False,
        "production_mutated": False,
        "verdict": "REVIEW_REQUIRED",
    }


def _parse_source(value: str) -> tuple[str, Path]:
    label, sep, path = value.partition("=")
    if not sep or not LABEL_RE.fullmatch(label) or not path:
        raise ValueError("source must have LABEL=/absolute/path/to/ledger.sqlite")
    p = Path(path)
    if not p.is_absolute():
        raise ValueError("absolute SQLite source path required")
    return label, p


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--current", required=True, help="Absolute existing ledger.sqlite")
    parser.add_argument("--historical", action="append", default=[],
                        help="LABEL=/absolute/existing/backup/ledger.sqlite; repeatable")
    parser.add_argument("--claimed-count", type=int)
    parser.add_argument("--claimed-cents", type=int)
    args = parser.parse_args()
    try:
        if (args.claimed_count is None) != (args.claimed_cents is None):
            raise ValueError("supply both claimed-count and claimed-cents")
        sources = [("current", Path(args.current))]
        if not sources[0][1].is_absolute():
            raise ValueError("absolute current DB path required")
        sources.extend(_parse_source(value) for value in args.historical)
        result = compare_snapshots(
            job_id=args.job_id, sources=sources,
            historical_claim=(
                (args.claimed_count, args.claimed_cents)
                if args.claimed_count is not None else None
            ),
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        # Never print SQLite values, local protected paths or exception text.
        print(json.dumps({"verdict": "BLOCKED", "error_type": type(exc).__name__,
                          "production_mutated": False, "paid_replay_authorized": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
