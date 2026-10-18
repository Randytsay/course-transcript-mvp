import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict

class TestGeminiPrimaryMixedStateRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.db_path)
        self.source_sha = "a" * 64
        self.job = self.ledger.create(
            source_sha=self.source_sha, mode="FLASH_CHIRP",
            estimated_stage_cents={"gemini_primary": 100, "chirp_independent": 50, "compare": 0, "third_optional": 0, "adjudication": 0, "golden_qa": 0}
        )
        self.ledger.approve(self.job, revision=0, approved_cents=500, reference="test-approval", actor="test-actor")
        self.job_id = self.job
        self.p_claim = self.ledger.claim_next(self.job_id, worker_id="test")
        self.orig_ev_sha = "b" * 64
        self.ledger.hold_for_review(self.p_claim, evidence_sha=self.orig_ev_sha, actual_cents=24)

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_reviewed_primary_in_mixed_state(self):
        # Suppose operator previously resumed or started chirp_independent while gemini_primary was REVIEW_REQUIRED
        with self.ledger._tx() as db:
            # Set stage 1 to RUNNING, job to PROCESSING, matching actual live mixed state
            db.execute("UPDATE v2_stages SET state='RUNNING',claim_hash='somehash' WHERE job_id=? AND name='chirp_independent'", (self.job_id,))
            db.execute("UPDATE v2_jobs SET status='PROCESSING' WHERE id=?", (self.job_id,))

        st = self.ledger.status(self.job_id)
        self.assertEqual(st["status"], "PROCESSING")
        self.assertEqual(st["stages"][0]["state"], "REVIEW_REQUIRED")
        self.assertEqual(st["stages"][1]["state"], "RUNNING")

        reviewed_sha = "c" * 64

        def verifier(**kwargs):
            return True

        self.ledger.complete_reviewed_primary(
            self.job_id,
            revision=st["revision"],
            reviewer="operator",
            review_reference="test-ref",
            previous_evidence_sha=self.orig_ev_sha,
            reviewed_evidence_sha=reviewed_sha,
            evidence_verifier=verifier,
        )

        st_after = self.ledger.status(self.job_id)
        self.assertEqual(st_after["status"], "PROCESSING")
        self.assertEqual(st_after["stages"][0]["state"], "COMPLETE")
        self.assertEqual(st_after["stages"][0]["evidence_sha"], reviewed_sha)
        self.assertEqual(st_after["stages"][1]["state"], "RUNNING")
        self.assertEqual(st_after["revision"], st["revision"] + 1)

if __name__ == "__main__":
    unittest.main()
