import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict

class TestLedgerChirpReviewRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.db_path)
        self.source_sha = "a" * 64
        self.job = self.ledger.create(
            source_sha=self.source_sha, mode="FLASH_CHIRP",
            estimated_stage_cents={"gemini_primary": 100, "chirp_independent": 50, "compare": 0, "third_optional": 0, "adjudication": 0, "golden_qa": 0}
        )
        self.ledger.approve(self.job, revision=0, approved_cents=500, reference="test-ref", actor="test-actor")
        self.job_id = self.job
        claim = self.ledger.claim_next(self.job_id, worker_id="test")
        self.ledger.complete(claim, evidence_sha="b" * 64, actual_cents=20)
        self.chirp_claim = self.ledger.claim_next(self.job_id, worker_id="test")
        self.ledger.plan_chirp_window(self.chirp_claim, {
            "window_id": "chunk-0000", "source_sha": self.source_sha,
            "clip_sha": "c" * 64, "gcs_uri": "gs://test/0.flac",
            "start_ms": 0, "end_ms": 10000, "output_uri": "gs://test/out/",
            "estimated_cents": 5,
        })
        self.sub = self.ledger.begin_chirp_submit(self.chirp_claim, "chunk-0000")
        self.ledger.record_chirp_submission(self.chirp_claim, "chunk-0000", "op-0")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_resolve_review_required(self):
        # 1. Record review required
        self.ledger.record_chirp_review_required(
            self.job_id, "chunk-0000",
            reason="invalid Speech word time offset",
            evidence_sha="d" * 64, actual_cents=5,
        )
        rows = self.ledger.chirp_windows(self.job_id)
        self.assertEqual(rows[0]["status"], "REVIEW_REQUIRED")
        self.assertEqual(rows[0]["result_sha"], "d" * 64)
        self.assertEqual(rows[0]["actual_cents"], 5)

        # 2. Reject resolve on sha mismatch
        with self.assertRaises(V2Conflict):
            self.ledger.resolve_chirp_window_review(
                self.job_id, "chunk-0000",
                proposal_sha="e" * 64, actual_cents=5,
                evidence_sha="f" * 64,
            )

        # 3. Resolve correctly
        self.ledger.resolve_chirp_window_review(
            self.job_id, "chunk-0000",
            proposal_sha="e" * 64, actual_cents=5,
            evidence_sha="d" * 64,
        )
        rows_after = self.ledger.chirp_windows(self.job_id)
        self.assertEqual(rows_after[0]["status"], "DONE")
        self.assertEqual(rows_after[0]["assessment_sha"], "e" * 64)
        self.assertEqual(rows_after[0]["actual_cents"], 5)

    def test_record_review_required_rejects_already_done(self):
        # 1. Transition window to DONE
        self.ledger.record_chirp_review_required(
            self.job_id, "chunk-0000",
            reason="initial test reason",
            evidence_sha="d" * 64, actual_cents=5,
        )
        self.ledger.resolve_chirp_window_review(
            self.job_id, "chunk-0000",
            proposal_sha="e" * 64, actual_cents=5,
            evidence_sha="d" * 64,
        )
        rows = self.ledger.chirp_windows(self.job_id)
        self.assertEqual(rows[0]["status"], "DONE")

        # 2. Assert that overwriting DONE with REVIEW_REQUIRED is strictly forbidden
        with self.assertRaises(V2Conflict):
            self.ledger.record_chirp_review_required(
                self.job_id, "chunk-0000",
                reason="illegal backward overwrite",
                evidence_sha="d" * 64, actual_cents=5,
            )

if __name__ == "__main__":
    unittest.main()
