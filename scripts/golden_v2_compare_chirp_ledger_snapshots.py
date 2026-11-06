"""Offline Golden V2 Chirp ledger snapshot comparator — local evidence, never GCP billing.

Reads EXISTING SQLite databases through mode=ro + query_only + a single BEGIN
transaction per file. Does NOT write any database, create a snapshot, open
GCP/Drive, trigger provider calls, or attest an original cloud operation.
Never prints raw operation names, request IDs, source hashes or provider paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3


JOB_RE = re.compile(r"v2p_[0-9a-f]{32}\Z")
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
COLUMNS = {"job_id", "window_id", "source_sha", "operation_name",
           "status", "actual_cents", "result_sha"}


def inspect_database(path: Path, job_id: str) -> dict:
    """Inventory one database in a transaction; private IDs never leave memory."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("database must be an existing regular non-symlink file")
    # SQLite mode=ro avoids accidental file creation. An explicit BEGIN gives a
    # consistent *per-database* multi-query view, not an across-file snapshot.
    with closing(sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True,
        isolation_level=None, timeout=5,
    )) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"v2_jobs", "v2_stages", "v2_chirp_operations"}.issubset(tables):
                return {"status": "SCHEMA_UNAVAILABLE",
                        "missing_tables": sorted(
                            {"v2_jobs", "v2_stages", "v2_chirp_operations"} - tables)}
            cols = {r["name"] for r in db.execute(
                "PRAGMA table_info(v2_chirp_operations)")}
            if not COLUMNS.issubset(cols):
                return {"status": "SCHEMA_UNAVAILABLE",
                        "missing_columns": sorted(COLUMNS - cols)}
            job = db.execute(
                "SELECT source_sha,revision,status FROM v2_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if job is None:
                return {"status": "JOB_NOT_IN_SNAPSHOT"}
            stage = db.execute(
                "SELECT state,actual_cents FROM v2_stages "
                "WHERE job_id=? AND name='chirp_independent'",
                (job_id,),
            ).fetchone()
            rows = db.execute(
                "SELECT window_id,source_sha,operation_name,status,"
                "actual_cents,result_sha FROM v2_chirp_operations "
                "WHERE job_id=? ORDER BY window_id", (job_id,),
            ).fetchall()

            by_name: dict[str, tuple[int | None, str | None]] = {}
            name_duplicates = 0
            names_missing = 0
            unknown_costs = 0
            invalid_costs = 0
            total_known = 0
            mismatched_source = 0
            missing_result = 0
            status_counts: Counter = Counter()
            window_ids: set[str] = set()
            duplicate_windows = 0
            for row in rows:
                status_counts[row["status"]] += 1
                if row["source_sha"] != job["source_sha"]:
                    mismatched_source += 1
                if not row["result_sha"]:
                    missing_result += 1
                cents = row["actual_cents"]
                if cents is None:
                    unknown_costs += 1
                elif type(cents) is not int or cents < 0:
                    invalid_costs += 1
                else:
                    total_known += cents
                if row["window_id"] in window_ids:
                    duplicate_windows += 1
                window_ids.add(row["window_id"])
                name = row["operation_name"]
                if not name:
                    names_missing += 1
                elif name in by_name:
                    name_duplicates += 1
                else:
                    by_name[name] = (cents, row["result_sha"])
            return {
                "status": "INSPECTED",
                "job_revision": job["revision"],
                "job_status": job["status"],
                "chirp_stage": (
                    {"state": stage["state"], "actual_cents": stage["actual_cents"]}
                    if stage is not None else None
                ),
                "operations": len(rows),
                "status_counts": dict(sorted(status_counts.items(), key=lambda x: str(x[0]))),
                "known_actual_cents": total_known,
                "unknown_actual_cents_count": unknown_costs,
                "invalid_actual_cents_count": invalid_costs,
                "missing_operation_names": names_missing,
                "duplicate_operation_names": name_duplicates,
                "duplicate_window_ids": duplicate_windows,
                "missing_result_sha": missing_result,
                "operation_source_sha_mismatch": mismatched_source,
                # Internal-only fields stripped by report(), not serialized.
                "_job_source_sha": job["source_sha"],
                "_operations_by_name": by_name,
            }
        finally:
            db.execute("ROLLBACK")


def compare_snapshots(
    current: dict, baselines: dict[str, dict],
    *, historical_claim_count: int | None = None,
    historical_claim_cents: int | None = None,
) -> dict:
    """Compare source-bound DB rows without conflating old claims or cloud billing."""
    def public_view(row: dict) -> dict:
        return {k: v for k, v in row.items() if not k.startswith("_")}

    if (historical_claim_count is None) != (historical_claim_cents is None):
        raise ValueError("historical count and cost must be supplied together")
    report = {
        "schema": "golden-v2-chirp-ledger-snapshot-compare-v1",
        "current": public_view(current),
        "baselines": {name: public_view(value) for name, value in baselines.items()},
        "baseline_differences": {},
        "historical_external_claim": (
            {"operations": historical_claim_count, "cents": historical_claim_cents,
             "basis": "unverified historical report, not validated provider billing"}
            if historical_claim_count is not None else None
        ),
        "cloud_billing_reconciled": False,
        "provider_operations_reconciled": False,
        "golden_qa_pass": False,
        "paid_or_delivery_authorized": False,
        "ledger_modified": False,
    }
    reasons = []
    if current.get("status") != "INSPECTED":
        reasons.append("CURRENT_LEDGER_NOT_INSPECTED")
    else:
        if historical_claim_count is None:
            reasons.append("EXTERNAL_HISTORICAL_CLAIM_NOT_PROVIDED")
        elif (current["operations"] != historical_claim_count
              or current["known_actual_cents"] != historical_claim_cents
              or current["unknown_actual_cents_count"]
              or current["invalid_actual_cents_count"]):
            reasons.append("HISTORICAL_EXTERNAL_CLAIM_NOT_RECONCILED")
        if current["missing_result_sha"]:
            reasons.append("CURRENT_RESULT_EVIDENCE_MISSING")
        if current["operation_source_sha_mismatch"]:
            reasons.append("CURRENT_OPERATION_SOURCE_MISMATCH")
        if (current["missing_operation_names"]
                or current["duplicate_operation_names"]
                or current["duplicate_window_ids"]
                or current["unknown_actual_cents_count"]
                or current["invalid_actual_cents_count"]):
            reasons.append("CURRENT_OPERATION_IDENTITIES_OR_COSTS_UNKNOWN")
        stage = current["chirp_stage"]
        if stage is None:
            reasons.append("CURRENT_CHIRP_STAGE_MISSING")
        elif (not current["unknown_actual_cents_count"]
              and not current["invalid_actual_cents_count"]
              and stage["actual_cents"] is not None
              and stage["actual_cents"] != current["known_actual_cents"]):
            reasons.append("CURRENT_STAGE_COST_DIFFERS_FROM_OPERATIONS")

    for label, past in baselines.items():
        if current.get("status") != "INSPECTED" or past.get("status") != "INSPECTED":
            report["baseline_differences"][label] = {"comparison": "NOT_COMPARABLE"}
            reasons.append("BASELINE_NOT_COMPARABLE")
            continue
        same_source = current["_job_source_sha"] == past["_job_source_sha"]
        identities_complete = same_source and not any((
            current["missing_operation_names"], current["duplicate_operation_names"],
            current["duplicate_window_ids"], current["operation_source_sha_mismatch"],
            past["missing_operation_names"], past["duplicate_operation_names"],
            past["duplicate_window_ids"], past["operation_source_sha_mismatch"],
        ))
        costs_complete = not any((
            current["unknown_actual_cents_count"], current["invalid_actual_cents_count"],
            past["unknown_actual_cents_count"], past["invalid_actual_cents_count"],
        ))
        cur = current["_operations_by_name"]
        old = past["_operations_by_name"]
        common = cur.keys() & old.keys() if identities_complete else set()
        comparison = {
            "comparison": (
                "SOURCE_MISMATCH" if not same_source else
                "SAME_JOB_SOURCE" if identities_complete else "INCOMPLETE_IDENTITIES"
            ),
            "same_source_sha": same_source,
            "identifiers_comparable": identities_complete,
            # Do not advertise exact set differences when IDs are missing,
            # duplicated, attached to another source, or otherwise ambiguous.
            "same_operation_name_count": len(common) if identities_complete else None,
            "only_current_operation_name_count": (
                len(cur.keys() - old.keys()) if identities_complete else None
            ),
            "only_baseline_operation_name_count": (
                len(old.keys() - cur.keys()) if identities_complete else None
            ),
            "changed_actual_cents_on_shared_name_count": (
                sum(cur[k][0] != old[k][0] for k in common)
                if identities_complete and costs_complete else None
            ),
            "changed_result_sha_on_shared_name_count": (
                sum(cur[k][1] != old[k][1] for k in common)
                if identities_complete else None
            ),
            "known_cents_delta_current_minus_baseline": (
                current["known_actual_cents"] - past["known_actual_cents"]
                if costs_complete else None
            ),
            "job_revision_delta_current_minus_baseline":
                current["job_revision"] - past["job_revision"],
        }
        report["baseline_differences"][label] = comparison
        if not same_source:
            reasons.append("BASELINE_JOB_SOURCE_MISMATCH")
        if not identities_complete:
            reasons.append("BASELINE_OPERATION_IDENTITIES_NOT_COMPARABLE")
        if not costs_complete:
            reasons.append("BASELINE_COST_UNKNOWN")
        if identities_complete and (
                comparison["only_baseline_operation_name_count"]
                or (comparison["changed_actual_cents_on_shared_name_count"] or 0)
                or comparison["changed_result_sha_on_shared_name_count"]):
            reasons.append("HISTORICAL_OPERATION_EVIDENCE_DIFFERS")
    report["unresolved"] = sorted(set(reasons + ["EXTERNAL_LRO_AND_BILLING_UNVERIFIED"]))
    report["status"] = "NO_GO"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument(
        "--baseline", action="append", default=[], metavar="LABEL=PATH",
        help="Existing historical SQLite file; repeat for PR158 and PR159",
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--historical-claim-count", type=int)
    parser.add_argument("--historical-claim-cents", type=int)
    args = parser.parse_args()
    if not JOB_RE.fullmatch(args.job_id):
        parser.error("invalid V2 job identifier")
    if (args.historical_claim_count is None) != (args.historical_claim_cents is None):
        parser.error("supply both historical claim count and cost, or neither")
    if (args.historical_claim_count is not None
            and (args.historical_claim_count < 0 or args.historical_claim_cents < 0)):
        parser.error("historical claim values must be nonnegative")
    snapshots = {}
    for item in args.baseline:
        label, sep, path = item.partition("=")
        if not sep or not NAME_RE.fullmatch(label) or not path or label in snapshots:
            parser.error("baseline format must be unique LABEL=PATH")
        snapshots[label] = Path(path)
    if not snapshots:
        parser.error("at least one historical baseline required")
    try:
        current = inspect_database(args.current, args.job_id)
        historical = {
            name: inspect_database(path, args.job_id)
            for name, path in snapshots.items()
        }
        result = compare_snapshots(
            current, historical,
            historical_claim_count=args.historical_claim_count,
            historical_claim_cents=args.historical_claim_cents,
        )
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        # Never print exception messages, SQLite paths or query contents.
        print(json.dumps({
            "status": "NO_GO", "error_type": type(exc).__name__,
            "cloud_billing_reconciled": False, "paid_or_delivery_authorized": False,
        }))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 2 if result["status"] == "NO_GO" else 0


if __name__ == "__main__":
    raise SystemExit(main())
