"""Provider-free Chirp worker restart and partial-cost regression tests."""
from __future__ import annotations

import hashlib
import json
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_chirp_recovery_worker import run_chirp_recovery_pass
from app.pipeline.golden_v2_chirp_stage import submit_planned_chirp
from tests import test_golden_v2_chirp_stage as chirp_fixtures


class ChirpRecoveryWorkerTests(unittest.TestCase):
    def setUp(self):
        # Reuse the real short FLAC / SQLite / fake provider fixture without
        # inheriting its tests, so these cover the production worker boundary.
        self.h = chirp_fixtures.ChirpStageIntegrationTests(methodName="test_restart_recovers_exact_operations_and_timing")
        self.h.setUp()
        self.addCleanup(self.h.tearDown)

    def recover(self, *, limit=8, cents=2, acoustic_inspector=None):
        return run_chirp_recovery_pass(
            ledger=self.h.ledger, job_id=self.h.job_id, job_dir=self.h.job_dir,
            gateway=self.h.gateway, cost_accountant=lambda _: cents,
            max_operations=limit,
            acoustic_inspector=(acoustic_inspector
                                or chirp_fixtures.passing_acoustic_inspector),
        )

    def test_restart_recovers_saved_operations_without_new_post(self):
        self.h.plan()
        rows = submit_planned_chirp(
            ledger=self.h.ledger, claim=self.h.claim, gateway=self.h.gateway)
        self.assertEqual(len(rows), 2)
        self.h._complete_outputs(rows)
        self.h.speech.operations[rows[1]["operation_name"]].done = False
        self.h.ledger = GoldenV2Ledger(self.h.ledger_path)

        first = self.recover(limit=1)
        self.assertEqual(first["action"], "WAITING_FOR_EXISTING_OPERATIONS")
        self.assertEqual(first["recovered"], 1)
        self.assertEqual(first["pending"], [rows[1]["window_id"]])

        self.h.speech.operations[rows[1]["operation_name"]].done = True
        second = self.recover()
        self.assertEqual(second["action"], "CHIRP_COMPLETE")
        self.assertEqual(second["word_count"], 2)
        self.assertEqual(second["acoustic_gate"], "PASS")
        self.assertEqual(self.recover()["action"], "ALREADY_COMPLETE")
        self.assertEqual(len(self.h.speech.calls), 2)

    def test_pending_operations_also_count_against_poll_limit(self):
        self.h.plan()
        rows = submit_planned_chirp(
            ledger=self.h.ledger, claim=self.h.claim, gateway=self.h.gateway)
        seen = []
        original_get = self.h.speech.get_operation

        def observed_get(name):
            seen.append(name)
            return original_get(name)

        self.h.speech.transport.operations_client.get_operation = observed_get
        outcome = self.recover(limit=1)
        self.assertEqual(outcome["action"], "WAITING_FOR_EXISTING_OPERATIONS")
        self.assertEqual(outcome["inspected"], 1)
        self.assertEqual(outcome["recovered"], 0)
        self.assertEqual(len(outcome["pending"]), 2)
        self.assertEqual(seen, [rows[0]["operation_name"]])
        self.assertEqual(len(self.h.speech.calls), 2)

    def test_uncertain_submission_never_retried_by_recovery(self):
        self.h.plan(max_window_seconds=10)
        self.h.speech.fail = True
        with self.assertRaises(TimeoutError):
            submit_planned_chirp(
                ledger=self.h.ledger, claim=self.h.claim, gateway=self.h.gateway)
        self.h.ledger = GoldenV2Ledger(self.h.ledger_path)
        result = self.recover()
        self.assertEqual(result["action"], "UNKNOWN_OPERATION_REQUIRES_REVIEW")
        self.assertEqual(result["unknown"], ["chunk-0000"])
        self.assertEqual(result["provider_submissions"], 0)
        self.assertEqual(len(self.h.speech.calls), 1)

    def test_verified_overrun_recovers_only_existing_operations(self):
        self.h.plan()
        rows = submit_planned_chirp(
            ledger=self.h.ledger, claim=self.h.claim, gateway=self.h.gateway)
        self.h._complete_outputs(rows)
        self.h.ledger = GoldenV2Ledger(self.h.ledger_path)
        first = self.recover(limit=1, cents=50)
        self.assertEqual(first["action"], "COST_RECONCILIATION_REQUIRED")
        self.assertEqual(first["recovered"], 1)
        second = self.recover(cents=2)
        self.assertEqual(second["action"], "COST_RECONCILIATION_REQUIRED")
        self.assertEqual([r["status"] for r in self.h.ledger.chirp_windows(self.h.job_id)],
                         ["DONE", "DONE"])
        self.assertEqual(len(self.h.speech.calls), 2)

    def test_complete_artifact_tamper_refuses_false_success(self):
        self.h.plan(max_window_seconds=10)
        rows = submit_planned_chirp(
            ledger=self.h.ledger, claim=self.h.claim, gateway=self.h.gateway)
        self.h._complete_outputs(rows)
        self.assertEqual(self.recover()["action"], "CHIRP_COMPLETE")
        artifact = self.h.job_dir / "golden-v2" / "chirp-merged.json"
        artifact.write_bytes(b"tampered")
        with self.assertRaisesRegex(V2Conflict, "missing or changed"):
            self.recover()

    def test_acoustic_review_keeps_chirp_stage_open_for_approved_repair(self):
        self.h.plan(max_window_seconds=10)
        rows = submit_planned_chirp(
            ledger=self.h.ledger, claim=self.h.claim, gateway=self.h.gateway)
        self.h._complete_outputs(rows)

        def review(*, job_dir, merged):
            digest = hashlib.sha256(json.dumps(
                merged, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            return {
                "schema": "golden-v2-acoustic-qa-v1", "status": "REVIEW",
                "source_sha256": merged["source_sha"], "merged_sha256": digest,
                "repair_plan": {"strategy": "STANDARD_BATCH", "submit_authorized": False,
                                "intervals": [{"start_ms": 0, "end_ms": 2500,
                                               "target_intervals": [{"start_ms": 200,
                                                                     "end_ms": 1000}]}],
                                "manual_review_required": False},
            }

        outcome = self.recover(acoustic_inspector=review)
        self.assertEqual(outcome["action"], "STANDARD_REPAIR_APPROVAL_REQUIRED")
        self.assertEqual(outcome["acoustic_gate"], "REVIEW")
        self.assertFalse((self.h.job_dir / "golden-v2" / "chirp-merged.json").exists())
        with self.assertRaisesRegex(V2Conflict, "unresolved: RUNNING"):
            self.h.ledger.claim_next(self.h.job_id, worker_id="compare-too-early")

    def test_invalid_limit_is_rejected(self):
        with self.assertRaisesRegex(V2Conflict, "bounded"):
            self.recover(limit=0)


if __name__ == "__main__":
    unittest.main()
