import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_chirp_stage import recover_review_required_chirp_window

class TestRecoverReviewRequiredChirpWindow(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="test-chirp-rec-"))
        self.db_path = self.tmpdir / "ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.db_path)
        self.source_sha = "a" * 64
        self.job = self.ledger.create(
            source_sha=self.source_sha, mode="FLASH_CHIRP",
            estimated_stage_cents={"gemini_primary": 100, "chirp_independent": 50, "compare": 0, "third_optional": 0, "adjudication": 0, "golden_qa": 0}
        )
        self.ledger.approve(self.job, revision=0, approved_cents=500, reference="test-approval", actor="test-actor")
        self.job_id = self.job
        claim = self.ledger.claim_next(self.job_id, worker_id="test")
        self.ledger.complete(claim, evidence_sha="b" * 64, actual_cents=20)
        chirp_claim = self.ledger.claim_next(self.job_id, worker_id="test")
        self.ledger.plan_chirp_window(chirp_claim, {
            "window_id": "chunk-0000", "source_sha": self.source_sha,
            "clip_sha": "c" * 64, "gcs_uri": "gs://test/0.flac",
            "start_ms": 0, "end_ms": 10000, "output_uri": "gs://test/out/",
            "estimated_cents": 5,
        })
        self.ledger.begin_chirp_submit(chirp_claim, "chunk-0000")
        self.ledger.record_chirp_submission(chirp_claim, "chunk-0000", "op-0")

        # Fake provider-raw.json
        raw = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "測試", "startOffset": "0s", "endOffset": "0.5s"},
                        {"word": "修復", "startOffset": "0.5s"},
                        {"word": "成功", "startOffset": "0.5s", "endOffset": "1.0s"},
                    ]
                }]
            }]
        }
        self.raw_bytes = json.dumps(raw).encode("utf-8")
        self.raw_sha = hashlib.sha256(self.raw_bytes).hexdigest()

        self.ev_dir = self.tmpdir / "golden-v2" / "chirp-evidence" / "chunk-0000"
        self.ev_dir.mkdir(parents=True)
        (self.ev_dir / "provider-raw.json").write_bytes(self.raw_bytes)

        self.ledger.record_chirp_review_required(
            self.job_id, "chunk-0000",
            reason="invalid Speech word time offset",
            evidence_sha=self.raw_sha, actual_cents=5,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_e2e_recovery(self):
        result = recover_review_required_chirp_window(
            ledger=self.ledger,
            job_id=self.job_id,
            window_id="chunk-0000",
            job_dir=self.tmpdir,
        )
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["window_id"], "chunk-0000")
        self.assertTrue((self.ev_dir / "window.json").is_file())
        self.assertTrue(list(self.ev_dir.glob("chirp-local-clock-fix-*.json")))
        rows = self.ledger.chirp_windows(self.job_id)
        self.assertEqual(rows[0]["status"], "DONE")
        self.assertEqual(rows[0]["actual_cents"], 5)
        self.assertEqual(rows[0]["result_sha"], self.raw_sha)

if __name__ == "__main__":
    unittest.main()
