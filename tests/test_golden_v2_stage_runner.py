"""Offline V2 integration: real SQLite ledger + comparison + QA artifacts."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_stage_runner import complete_comparison, complete_qa_or_hold


W = [{"word": "復次", "start_ms": 100, "end_ms": 400},
     {"word": "文殊師利", "start_ms": 400, "end_ms": 1200},
     {"word": "男身具七寶", "start_ms": 1200, "end_ms": 2400}]
SRT = ("1\n00:00:00,100 --> 00:00:00,400\n復次\n\n"
       "2\n00:00:00,400 --> 00:00:01,200\n文殊師利\n\n"
       "3\n00:00:01,200 --> 00:00:02,400\n男身具七寶\n")


class V2DurableBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.job_dir = Path(self.temp.name) / "artifacts"
        self.db = Path(self.temp.name) / "state.db"
        self.store = GoldenV2Ledger(self.db)
        self.job = self.store.create(
            source_sha="1" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 10, "chirp_independent": 20,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.store.approve(self.job, revision=0, approved_cents=30,
                           actor="owner-test", reference="explicit-fake-test")
        golden_dir = self.job_dir / "golden-v2"
        golden_dir.mkdir(parents=True)
        artifacts = {
            "gemini_primary": ("primary-stage.json", {
                "schema": "golden-v2-primary-stage-v1", "job_id": self.job,
                "source_sha256": "1" * 64, "ready_for_chirp": True,
                "assembled": {"text": "復次文殊師利男身具七寶"}}),
            "chirp_independent": ("chirp-merged.json", {
                "schema": "golden-v2-chirp-merged-v1", "job_id": self.job,
                "source_sha": "1" * 64, "timing_authority": "chirp_3",
                "words": W}),
        }
        for stage in ("gemini_primary", "chirp_independent"):
            claim = self.store.claim_next(self.job, worker_id="fake")
            self.assertEqual(claim["stage"], stage)
            name, artifact = artifacts[stage]
            wire = json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode()
            (golden_dir / name).write_bytes(wire)
            self.store.complete(claim, evidence_sha=hashlib.sha256(wire).hexdigest(),
                                actual_cents=0)

    def tearDown(self):
        self.temp.cleanup()

    def test_compare_survives_restart_and_qa_pass_does_not_publish(self):
        claim = self.store.claim_next(self.job, worker_id="fake")
        self.assertEqual(claim["stage"], "compare")
        report = complete_comparison(
            ledger=self.store, claim=claim, job_dir=self.job_dir,
            primary_text="復次文殊師利男身具七寶", chirp_words=W,
        )
        self.assertEqual(report["pending_review_count"], 0)
        self.assertTrue((self.job_dir / "golden-v2/engine-comparison.json").exists())
        recovered = GoldenV2Ledger(self.db)
        next_claim = recovered.claim_next(self.job, worker_id="new-worker")
        self.assertEqual(next_claim["stage"], "adjudication")  # third-model SKIPPED
        recovered.complete(next_claim, evidence_sha="b"*64, actual_cents=0)
        qa_claim = recovered.claim_next(self.job, worker_id="new-worker")
        qa = complete_qa_or_hold(
            ledger=recovered, claim=qa_claim, job_dir=self.job_dir,
            srt=SRT, transcript="復次文殊師利男身具七寶",
            chirp_word_boundaries_ms={100, 400, 1200, 2400},
            acoustic_gate="PASS", semantic_gate="PASS",
            comparison=report, adjudications=[],
        )
        self.assertEqual(qa["status"], "PASS")
        self.assertEqual(recovered.status(self.job)["stages"][-1]["state"], "COMPLETE")
        with self.assertRaises(V2Conflict):
            recovered.claim_next(self.job, worker_id="new-worker")
        self.assertEqual(recovered.status(self.job)["status"], "AWAITING_FINAL_REVIEW")
        self.assertEqual(list(self.job_dir.rglob("*.srt")), [])  # no auto Drive publish

    def test_qa_failure_holds_job_with_evidence(self):
        claim = self.store.claim_next(self.job, worker_id="fake")
        report = complete_comparison(
            ledger=self.store, claim=claim, job_dir=self.job_dir,
            primary_text="復次文殊師利男身具七寶", chirp_words=W,
        )
        self.store.complete(self.store.claim_next(self.job, worker_id="fake"),
                            evidence_sha="b"*64, actual_cents=0)
        qa_claim = self.store.claim_next(self.job, worker_id="fake")
        qa = complete_qa_or_hold(
            ledger=self.store, claim=qa_claim, job_dir=self.job_dir,
            srt=SRT, transcript="復次文殊師利男身具七寶",
            chirp_word_boundaries_ms={100, 400, 1200, 2400},
            acoustic_gate="UNVERIFIED", semantic_gate="PASS",
            comparison=report, adjudications=[],
        )
        self.assertEqual(qa["status"], "BLOCKED")
        self.assertEqual(self.store.status(self.job)["status"], "NEEDS_REVIEW")
        self.assertEqual(self.store.status(self.job)["stages"][-1]["state"], "REVIEW_REQUIRED")
        with self.assertRaises(V2Conflict):
            self.store.claim_next(self.job, worker_id="fake")
        self.assertTrue((self.job_dir / "golden-v2/golden-qa.json").is_file())


if __name__ == "__main__":
    unittest.main()
