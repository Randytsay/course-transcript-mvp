"""Synthetic-only regression checks; never open a real Production SQLite file."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.golden_v2_ledger_snapshot_reconcile import (
    compare_snapshots, inspect_snapshot,
)

JOB = "v2p_" + "a" * 32
SOURCE = "b" * 64


def create_snapshot(path: Path, *, n: int, cents: int, source: str = SOURCE,
                    first: int = 0, missing_operation: bool = False,
                    missing_actual: bool = False, schema_ok: bool = True) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE v2_jobs (id TEXT PRIMARY KEY,source_sha TEXT,"
            "status TEXT,revision INTEGER)"
        )
        db.execute(
            "INSERT INTO v2_jobs VALUES (?, ?, 'NEEDS_REVIEW', 5)",
            (JOB, source),
        )
        db.execute(
            "CREATE TABLE v2_stages (job_id TEXT,name TEXT,state TEXT,"
            "actual_cents INTEGER)"
        )
        db.execute(
            "INSERT INTO v2_stages VALUES (?, 'chirp_independent',"
            "'REVIEW_REQUIRED', ?)", (JOB, cents)
        )
        if not schema_ok:
            return
        db.execute(
            "CREATE TABLE v2_chirp_operations (job_id TEXT,window_id TEXT,"
            "source_sha TEXT,operation_name TEXT,status TEXT,"
            "actual_cents INTEGER,estimated_cents INTEGER)"
        )
        allocations = [cents // n] * n
        for i in range(cents % n):
            allocations[i] += 1
        for index in range(n):
            pos = first + index
            db.execute(
                "INSERT INTO v2_chirp_operations VALUES (?,?,?,?,?,?,?)",
                (JOB, "win-" + str(pos), source,
                 None if (missing_operation and index == 0) else
                 "projects/test/locations/test/operations/name-" + str(pos),
                 "DONE",
                 None if (missing_actual and index == 0) else allocations[index],
                 allocations[index]),
            )


class GoldenV2LedgerSnapshotReconcileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.current = self.root / "current.sqlite"
        self.historical = self.root / "historical.sqlite"

    def sources(self):
        return [("current", self.current), ("pr159", self.historical)]

    def test_historical_34_to_current_16_remains_open_without_raw_operation_names(self):
        create_snapshot(self.current, n=16, cents=78)
        create_snapshot(self.historical, n=34, cents=136)
        before = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.current, self.historical)
        }
        report = compare_snapshots(
            job_id=JOB, sources=self.sources(), historical_claim=(34, 136)
        )
        self.assertEqual(report["verdict"], "REVIEW_REQUIRED")
        self.assertEqual(report["gcp_billing"], "UNKNOWN")
        self.assertFalse(report["cross_file_atomic"])
        self.assertFalse(report["reconciliation_complete"])
        self.assertFalse(report["paid_replay_authorized"])
        self.assertEqual(report["snapshots"][0]["operation_rows"], 16)
        self.assertEqual(report["snapshots"][0]["known_actual_cents"], 78)
        self.assertEqual(report["snapshots"][1]["operation_rows"], 34)
        self.assertEqual(report["snapshots"][1]["known_actual_cents"], 136)
        difference = report["historical_comparisons"][0]
        self.assertTrue(difference["source_sha_same"])
        self.assertEqual(difference["current_rows_minus_historical"], -18)
        self.assertEqual(difference["historical_minus_current_operation_names"], 18)
        self.assertEqual(difference["common_operation_names"], 16)
        self.assertEqual(difference["cost_delta_known_cents"], -58)
        self.assertEqual(report["historical_claim_comparison"]["current_count_difference"], -18)
        self.assertEqual(report["historical_claim_comparison"]["current_known_cents_difference"], -58)
        self.assertNotIn("projects/test/locations/test/operations", repr(report))
        self.assertNotIn(str(self.root), repr(report))
        for path in (self.current, self.historical):
            self.assertEqual(before[path], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_equal_snapshots_never_claim_gcp_billing_or_golden_pass(self):
        create_snapshot(self.current, n=16, cents=78)
        create_snapshot(self.historical, n=16, cents=78)
        result = compare_snapshots(job_id=JOB, sources=self.sources())
        self.assertEqual(result["historical_comparisons"][0]["common_operation_names"], 16)
        self.assertEqual(result["historical_comparisons"][0]["current_minus_historical_operation_names"], 0)
        self.assertFalse(result["golden_qa_pass"])
        self.assertEqual(result["external_provider_operations"], "NOT_QUERIED")
        self.assertEqual(result["missing_external_operations"], "UNKNOWN")

    def test_incomplete_operation_identity_fails_closed(self):
        create_snapshot(self.current, n=16, cents=78, missing_operation=True)
        create_snapshot(self.historical, n=34, cents=136)
        report = compare_snapshots(job_id=JOB, sources=self.sources())
        self.assertFalse(report["historical_comparisons"][0]["identifiers_comparable"])
        self.assertIsNone(report["historical_comparisons"][0]["common_operation_names"])
        self.assertEqual(report["snapshots"][0]["operation_names_missing"], 1)

    def test_missing_cost_never_fabricates_difference(self):
        create_snapshot(self.current, n=16, cents=78, missing_actual=True)
        create_snapshot(self.historical, n=34, cents=136)
        report = compare_snapshots(
            job_id=JOB, sources=self.sources(), historical_claim=(34, 136)
        )
        self.assertEqual(report["snapshots"][0]["actual_cents_unknown_rows"], 1)
        self.assertIsNone(report["historical_comparisons"][0]["cost_delta_known_cents"])
        self.assertIsNone(report["historical_claim_comparison"]["current_known_cents_difference"])

    def test_source_sha_conflict_is_explicit_and_not_reconciled(self):
        create_snapshot(self.current, n=16, cents=78)
        create_snapshot(self.historical, n=34, cents=136, source="c" * 64)
        report = compare_snapshots(job_id=JOB, sources=self.sources())
        self.assertFalse(report["historical_comparisons"][0]["source_sha_same"])
        self.assertFalse(report["reconciliation_complete"])

    def test_incomplete_older_schema_is_reported_without_guessing(self):
        create_snapshot(self.current, n=16, cents=78)
        create_snapshot(self.historical, n=1, cents=1, schema_ok=False)
        report = compare_snapshots(job_id=JOB, sources=self.sources())
        self.assertEqual(report["snapshots"][1]["evidence_state"], "SCHEMA_INCOMPATIBLE")
        self.assertIsNone(report["historical_comparisons"][0]["current_rows_minus_historical"])

    def test_reject_unknown_job_and_symlink_database(self):
        create_snapshot(self.current, n=16, cents=78)
        with self.assertRaisesRegex(ValueError, "invalid V2 job"):
            inspect_snapshot(self.current, "totally-invalid")
        symlink = self.root / "shortcut.sqlite"
        symlink.symlink_to(self.current)
        with self.assertRaisesRegex(ValueError, "existing regular SQLite file"):
            inspect_snapshot(symlink, JOB)

    def test_missing_older_job_is_unknown_not_zero(self):
        create_snapshot(self.current, n=16, cents=78)
        create_snapshot(self.historical, n=1, cents=1)
        with sqlite3.connect(self.historical) as db:
            db.execute("DELETE FROM v2_chirp_operations")
            db.execute("DELETE FROM v2_stages")
            db.execute("DELETE FROM v2_jobs")
        report = compare_snapshots(job_id=JOB, sources=self.sources())
        self.assertEqual(report["snapshots"][1]["evidence_state"], "JOB_NOT_PRESENT")
        self.assertIsNone(report["historical_comparisons"][0]["current_rows_minus_historical"])


if __name__ == "__main__":
    unittest.main()
