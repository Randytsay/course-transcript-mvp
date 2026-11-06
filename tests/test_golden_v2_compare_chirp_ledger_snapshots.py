"""Offline tests for read-only, source-bound historical Chirp ledger comparison."""
import os
import contextlib
import io
import json
import sys
from unittest.mock import patch
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.golden_v2_compare_chirp_ledger_snapshots import (
    compare_snapshots, inspect_database, main,
)


class ChirpSnapshotComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = "v2p_" + "e" * 32

    def build(self, name, *, count, costs, sha="a" * 64, revision=5,
              operation_offset=0, missing_name=False, stage_actual=None):
        path = self.root / (name + ".sqlite")
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE v2_jobs (
                    id TEXT, source_sha TEXT, revision INTEGER, status TEXT
                );
                CREATE TABLE v2_stages (
                    job_id TEXT, name TEXT, state TEXT, actual_cents INTEGER
                );
                CREATE TABLE v2_chirp_operations (
                    job_id TEXT, window_id TEXT, source_sha TEXT,
                    operation_name TEXT, status TEXT, actual_cents INTEGER,
                    result_sha TEXT
                );
            """)
            db.execute("INSERT INTO v2_jobs VALUES(?,?,?,?)",
                       (self.job, sha, revision, "NEEDS_REVIEW"))
            total = sum(c for c in costs if c is not None)
            db.execute("INSERT INTO v2_stages VALUES(?,?,?,?)",
                       (self.job, "chirp_independent", "REVIEW_REQUIRED",
                        total if stage_actual is None else stage_actual))
            for i in range(count):
                db.execute("INSERT INTO v2_chirp_operations VALUES(?,?,?,?,?,?,?)",
                           (self.job, "w" + str(i), sha,
                            None if missing_name and i == 0
                            else "operation" + str(i + operation_offset),
                            "DONE", costs[i], "f" * 64))
        return path

    def test_34_vs_16_rows_do_not_become_cloud_reconciled(self):
        base = self.build("old", count=34, costs=[4] * 34, revision=4)
        current = self.build(
            "now", count=16, costs=[5] * 14 + [4] * 2, revision=5
        )
        a = inspect_database(current, self.job)
        b = inspect_database(base, self.job)
        report = compare_snapshots(
            a, {"PR159": b},
            historical_claim_count=34, historical_claim_cents=136,
        )
        self.assertEqual(report["current"]["operations"], 16)
        self.assertEqual(report["current"]["known_actual_cents"], 78)
        self.assertEqual(report["baselines"]["PR159"]["operations"], 34)
        self.assertEqual(report["baselines"]["PR159"]["known_actual_cents"], 136)
        diff = report["baseline_differences"]["PR159"]
        self.assertEqual(diff["same_operation_name_count"], 16)
        self.assertEqual(diff["only_baseline_operation_name_count"], 18)
        self.assertEqual(diff["known_cents_delta_current_minus_baseline"], -58)
        self.assertEqual(diff["changed_actual_cents_on_shared_name_count"], 14)
        self.assertEqual(report["status"], "NO_GO")
        self.assertEqual(report["historical_external_claim"]["operations"], 34)
        self.assertFalse(report["cloud_billing_reconciled"])
        self.assertFalse(report["provider_operations_reconciled"])
        self.assertFalse(report["paid_or_delivery_authorized"])
        self.assertNotIn("operation0", repr(report))
        self.assertNotIn("a" * 64, repr(report))
        self.assertNotIn(str(current), repr(report))

    def test_unknown_identity_and_cost_never_silently_zero(self):
        path = self.build("unknown", count=2, costs=[None, 2],
                          missing_name=True)
        a = inspect_database(path, self.job)
        self.assertEqual(a["unknown_actual_cents_count"], 1)
        self.assertEqual(a["known_actual_cents"], 2)
        self.assertEqual(a["missing_operation_names"], 1)
        report = compare_snapshots(a, {"before": a})
        self.assertIn("CURRENT_OPERATION_IDENTITIES_OR_COSTS_UNKNOWN",
                      report["unresolved"])
        self.assertIsNone(report["historical_external_claim"])
        self.assertEqual(report["status"], "NO_GO")

    def test_mismatched_sources_and_stage_cost_are_blockers(self):
        current = inspect_database(self.build(
            "current", count=2, costs=[2, 2], stage_actual=1
        ), self.job)
        old = inspect_database(self.build(
            "previous", count=2, costs=[2, 2], sha="b" * 64
        ), self.job)
        report = compare_snapshots(current, {"old": old})
        self.assertEqual(
            report["baseline_differences"]["old"]["comparison"],
            "SOURCE_MISMATCH",
        )
        self.assertIn("BASELINE_JOB_SOURCE_MISMATCH", report["unresolved"])
        self.assertIn("CURRENT_STAGE_COST_DIFFERS_FROM_OPERATIONS",
                      report["unresolved"])

    def test_missing_legacy_schema_is_unknown_not_fake_zero(self):
        path = self.root / "early.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE v2_jobs (id TEXT, source_sha TEXT)")
        a = inspect_database(path, self.job)
        self.assertEqual(a["status"], "SCHEMA_UNAVAILABLE")
        b = inspect_database(self.build(
            "current", count=1, costs=[4],
        ), self.job)
        report = compare_snapshots(b, {"old": a})
        self.assertEqual(
            report["baseline_differences"]["old"]["comparison"],
            "NOT_COMPARABLE",
        )
        self.assertIn("BASELINE_NOT_COMPARABLE", report["unresolved"])

    def test_inspection_itself_does_not_change_file(self):
        path = self.build("safe", count=1, costs=[4])
        before = path.read_bytes()
        inspect_database(path, self.job)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / "safe.sqlite-wal").exists())
        self.assertFalse((self.root / "safe.sqlite-shm").exists())


    def test_live_wal_sidecar_is_rejected_before_read(self):
        path = self.build("has_wal", count=1, costs=[4])
        sidecar = Path(str(path) + "-wal")
        sidecar.write_bytes(b"fixture-no-live-sqlite")
        with self.assertRaisesRegex(ValueError, "WAL/SHM sidecars"):
            inspect_database(path, self.job)
        self.assertEqual(sidecar.read_bytes(), b"fixture-no-live-sqlite")

    def test_cli_rejects_same_db_for_current_and_historical_baseline(self):
        path = self.build("same_file", count=1, costs=[4])
        args = ["reconcile", "--current", str(path),
                "--baseline", "PR159=" + str(path),
                "--job-id", self.job]
        captured = io.StringIO()
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(captured):
            rc = main()
        self.assertEqual(rc, 2)
        redacted = json.loads(captured.getvalue())
        self.assertEqual(redacted["status"], "NO_GO")
        self.assertFalse(redacted["paid_or_delivery_authorized"])
        self.assertNotIn(str(path), captured.getvalue())

    def test_missing_identity_never_claims_exact_operation_set_deltas(self):
        current = inspect_database(self.build(
            "current_missing_id", count=2, costs=[2, 2], missing_name=True
        ), self.job)
        previous = inspect_database(self.build(
            "older_complete", count=4, costs=[2] * 4
        ), self.job)
        report = compare_snapshots(current, {"older": previous})
        diff = report["baseline_differences"]["older"]
        self.assertFalse(diff["identifiers_comparable"])
        self.assertEqual(diff["comparison"], "INCOMPLETE_IDENTITIES")
        self.assertIsNone(diff["only_baseline_operation_name_count"])
        self.assertIsNone(diff["same_operation_name_count"])
        self.assertIn("BASELINE_OPERATION_IDENTITIES_NOT_COMPARABLE",
                      report["unresolved"])

    def test_unknown_actual_cost_never_becomes_fake_delta(self):
        current = inspect_database(self.build(
            "current_unknown_cost", count=2, costs=[None, 2]
        ), self.job)
        previous = inspect_database(self.build(
            "older_cost_known", count=2, costs=[1, 2]
        ), self.job)
        report = compare_snapshots(current, {"older": previous},
                                   historical_claim_count=34,
                                   historical_claim_cents=136)
        diff = report["baseline_differences"]["older"]
        self.assertIsNone(diff["known_cents_delta_current_minus_baseline"])
        self.assertIsNone(diff["changed_actual_cents_on_shared_name_count"])
        self.assertIn("BASELINE_COST_UNKNOWN", report["unresolved"])
        self.assertFalse(report["cloud_billing_reconciled"])

    def test_negative_cost_is_not_summed_as_real_spend(self):
        current = inspect_database(self.build(
            "bad_cost", count=2, costs=[-2, 4]
        ), self.job)
        previous = inspect_database(self.build(
            "older_valid", count=2, costs=[2, 4]
        ), self.job)
        self.assertEqual(current["invalid_actual_cents_count"], 1)
        self.assertEqual(current["known_actual_cents"], 4)
        report = compare_snapshots(current, {"older": previous})
        self.assertIsNone(
            report["baseline_differences"]["older"]["known_cents_delta_current_minus_baseline"]
        )
        self.assertIn("BASELINE_COST_UNKNOWN", report["unresolved"])


if __name__ == "__main__":
    unittest.main()
