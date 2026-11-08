"""Offline Chirp operation persistence, duplicate prevention and restart gates."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict


class ChirpOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "v2.db"
        self.ledger = GoldenV2Ledger(self.path)
        self.job = self.ledger.create(
            source_sha="a" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 1, "chirp_independent": 10,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(self.job, revision=0, approved_cents=11,
                            reference="fake-only", actor="mock-owner")
        gemini = self.ledger.claim_next(self.job, worker_id="test")
        self.ledger.complete(gemini, evidence_sha="e" * 64, actual_cents=1)
        self.claim = self.ledger.claim_next(self.job, worker_id="test")
        self.window = {
            "window_id": "chunk-000", "source_sha": "a" * 64,
            "clip_sha": "b" * 64,
            "gcs_uri": "gs://private-test/audio.flac",
            "start_ms": 0, "end_ms": 15000,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_durable_submitting_blocks_duplicate_after_restart(self):
        self.ledger.plan_chirp_window(self.claim, self.window)
        self.ledger.plan_chirp_window(self.claim, self.window)
        self.ledger.begin_chirp_submit(self.claim, "chunk-000")
        restarted = GoldenV2Ledger(self.path)
        self.assertEqual(restarted.chirp_windows(self.job)[0]["status"], "SUBMITTING")
        with self.assertRaises(V2Conflict):
            restarted.begin_chirp_submit(self.claim, "chunk-000")
        with self.assertRaises(V2Conflict):
            restarted.claim_next(self.job, worker_id="other")
        with self.assertRaises(V2Conflict):
            restarted.plan_chirp_window(self.claim, {**self.window, "clip_sha": "c" * 64})
        with self.assertRaises(V2Conflict):
            restarted.recover_submitted_chirp(
                self.job, "chunk-000", operation_name="operations/abc",
                evidence_sha="d" * 64, actual_cents=4, verifier=lambda **_: True)

    def test_submitted_operation_recovered_without_resubmission(self):
        self.ledger.plan_chirp_window(self.claim, self.window)
        self.ledger.begin_chirp_submit(self.claim, "chunk-000")
        self.ledger.record_chirp_submission(self.claim, "chunk-000", "operations/abc")
        resumed = GoldenV2Ledger(self.path)
        with self.assertRaises(V2Conflict):
            resumed.recover_submitted_chirp(
                self.job, "chunk-000", operation_name="operations/wrong",
                evidence_sha="d" * 64, actual_cents=4, verifier=lambda **_: True)
        with self.assertRaises(V2Conflict):
            resumed.recover_submitted_chirp(
                self.job, "chunk-000", operation_name="operations/abc",
                evidence_sha="d" * 64, actual_cents=4, verifier=lambda **_: False)
        self.assertEqual(resumed.chirp_windows(self.job)[0]["status"], "SUBMITTED")
        resumed.recover_submitted_chirp(
            self.job, "chunk-000", operation_name="operations/abc",
            evidence_sha="d" * 64, actual_cents=4,
            verifier=lambda **row: row["clip_sha"] == "b" * 64 and row["start_ms"] == 0)
        resumed.recover_submitted_chirp(
            self.job, "chunk-000", operation_name="operations/abc",
            evidence_sha="d" * 64, actual_cents=4, verifier=lambda **_: True)
        with self.assertRaises(V2Conflict):
            resumed.finish_recovered_chirp(
                self.job, evidence_sha="f" * 64, verifier=lambda **_: False)
        resumed.finish_recovered_chirp(
            self.job, evidence_sha="f" * 64,
            verifier=lambda **row: len(row["windows"]) == 1
            and row["windows"][0]["result_sha"] == "d" * 64)
        self.assertEqual(resumed.status(self.job)["spent_cents"], 5)
        self.assertEqual(resumed.claim_next(self.job, worker_id="next")["stage"], "compare")

    def test_cost_caps_duration_and_result_verifier(self):
        with self.assertRaises(V2Conflict):
            self.ledger.plan_chirp_window(self.claim, {**self.window, "end_ms": 1200001})
        self.ledger.plan_chirp_window(self.claim, self.window)
        with self.assertRaises(V2Conflict):
            self.ledger.complete_chirp_operations(
                self.claim, evidence_sha="f" * 64, verifier=lambda **_: True)
        self.ledger.begin_chirp_submit(self.claim, "chunk-000")
        self.ledger.record_chirp_submission(self.claim, "chunk-000", "operations/abc")
        with self.assertRaises(V2Conflict):
            self.ledger.record_chirp_result(
                self.claim, "chunk-000", operation_name="operations/abc",
                evidence_sha="d" * 64, actual_cents=4, verifier=lambda **_: False)
        self.ledger.recover_submitted_chirp(
            self.job, "chunk-000", operation_name="operations/abc",
            evidence_sha="d" * 64, actual_cents=11, verifier=lambda **_: True)
        restarted = GoldenV2Ledger(self.path)
        self.assertEqual(restarted.chirp_windows(self.job)[0]["status"], "DONE")
        self.assertEqual(restarted.status(self.job)["stages"][1]["state"], "OVER_BUDGET")
        self.assertEqual(restarted.status(self.job)["spent_cents"], 12)
        self.assertEqual(restarted.status(self.job)["status"], "NEEDS_RECONCILIATION")
        with self.assertRaises(V2Conflict):
            restarted.claim_next(self.job, worker_id="other")


if __name__ == "__main__":
    unittest.main()
