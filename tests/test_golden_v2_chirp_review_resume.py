"""Provider-free regression for CAS repair rounds on completed Chirp jobs."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict


JOB_SHA = "a" * 64
OLD_CHIRP = "b" * 64
OLD_QA = "c" * 64


class RepairRoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = GoldenV2Ledger(Path(self.tmp.name) / "v2.sqlite")
        self.job = self.ledger.create(
            source_sha=JOB_SHA, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 200, "chirp_independent": 50,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(self.job, revision=0, approved_cents=250,
                            reference="test-owner-approval", actor="test-owner")
        c = self.ledger.claim_next(self.job, worker_id="test", expected_stage="gemini_primary")
        self.ledger.complete(c, evidence_sha="d"*64, actual_cents=20)
        c = self.ledger.claim_next(self.job, worker_id="test", expected_stage="chirp_independent")
        with self.ledger._tx() as db:
            db.execute(
                "INSERT INTO v2_chirp_operations "
                "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,"
                "status,operation_name,request_id,output_uri,estimated_cents,"
                "purpose,result_sha,actual_cents,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.job, "chunk-0000", JOB_SHA, "e"*64, "gs://test/input",
                 0, 10000, "DONE", "ops/test", "f"*64, "gs://test/output/",
                 50, "BASE", "1"*64, 50, "2026-10-20T00:00:00+00:00"),
            )
        self.ledger.complete(c, evidence_sha=OLD_CHIRP, actual_cents=50)
        c = self.ledger.claim_next(self.job, worker_id="test", expected_stage="compare")
        self.ledger.complete(c, evidence_sha="2"*64, actual_cents=0,
                             comparison_has_dispute=False)
        # Third stage is automatically skipped for FLASH_CHIRP.
        c = self.ledger.claim_next(self.job, worker_id="test", expected_stage="adjudication")
        self.ledger.complete(c, evidence_sha="3"*64, actual_cents=0)
        c = self.ledger.claim_next(self.job, worker_id="test", expected_stage="golden_qa")
        self.ledger.hold_for_review(c, evidence_sha=OLD_QA)
        self.before = self.ledger.status(self.job)
        self.calls = 0

    def verified(self, **kwargs):
        self.calls += 1
        return (kwargs["job_id"] == self.job and
                kwargs["source_sha"] == JOB_SHA and
                kwargs["old_chirp_sha"] == OLD_CHIRP and
                kwargs["old_qa_sha"] == OLD_QA and
                len(kwargs["windows"]) == 1 and
                kwargs["windows"][0]["result_sha"] == "1"*64)

    def resume(self, **kwargs):
        args = dict(job_id=self.job, revision=self.before["revision"],
                    source_sha=JOB_SHA,
                    previous_chirp_evidence_sha=OLD_CHIRP,
                    previous_qa_evidence_sha=OLD_QA,
                    transfer_cents=40, reviewer="test-owner",
                    review_reference="audio-review-2026-10-20",
                    worker_id="test-worker", aggregate_cap_cents=250,
                    evidence_verifier=self.verified)
        args.update(kwargs)
        return self.ledger.resume_chirp_review_for_repair(**args)

    def test_repair_resume_preserves_raw_budget_and_snapshots(self):
        claim = self.resume()
        self.ledger.assert_stage_claim(claim, stage="chirp_independent")
        after = self.ledger.status(self.job)
        self.assertEqual(after["status"], "PROCESSING")
        self.assertEqual(after["revision"], self.before["revision"]+1)
        self.assertEqual([x["state"] for x in after["stages"]],
                         ["COMPLETE","RUNNING","PENDING","SKIPPED","PENDING","PENDING"])
        self.assertEqual([x["estimate_cents"] for x in after["stages"]],
                         [160,90,0,0,0,0])
        self.assertEqual(after["spent_cents"], self.before["spent_cents"])
        with sqlite3.connect(self.ledger.database) as db:
            rounds = db.execute(
                "SELECT prior_stage_snapshot_json,transfer_cents FROM "
                "v2_chirp_repair_rounds WHERE job_id=?", (self.job,),
            ).fetchall()
            self.assertEqual(len(rounds), 1)
            self.assertEqual(rounds[0][1], 40)
            self.assertEqual(
                [r["state"] for r in __import__("json").loads(rounds[0][0])],
                [x["state"] for x in self.before["stages"]],
            )
            self.assertEqual(
                db.execute("SELECT status,result_sha,actual_cents FROM "
                           "v2_chirp_operations WHERE job_id=?", (self.job,)).fetchone(),
                ("DONE", "1"*64, 50),
            )
        with self.assertRaises(V2Conflict):
            self.resume()
        assessment_sha = "4"*64
        window = {
            "window_id": "repair-0000", "source_sha": JOB_SHA,
            "clip_sha": "5"*64, "gcs_uri": "gs://test/repair-input",
            "output_uri": "gs://test/repair-output/",
            "start_ms": 30000, "end_ms": 35000,
            "estimated_cents": 1,
            "assessment_sha": assessment_sha,
            "target_intervals": [{"start_ms": 31000, "end_ms": 34000}],
            "strategy": "STANDARD_BATCH",
        }
        plan = self.ledger.approve_chirp_repair_plan(
            self.job, expected_revision=after["revision"],
            assessment_sha=assessment_sha, windows=[window],
            approved_cents=1, reference="test-plan", actor="test-owner",
        )
        self.ledger.plan_chirp_repair_windows(
            claim, plan_sha=plan["plan_sha"], windows=[window],
        )
        self.assertEqual(len(self.ledger.chirp_windows(self.job)), 2)

    def test_stale_revision_and_changed_sha_fail_without_mutation(self):
        for values in [
            dict(revision=self.before["revision"]-1),
            dict(source_sha="0"*64),
            dict(previous_qa_evidence_sha="9"*64),
            dict(transfer_cents=181),
            dict(aggregate_cap_cents=69),
            dict(evidence_verifier=lambda **_: False),
        ]:
            with self.subTest(values=values):
                with self.assertRaises(V2Conflict):
                    self.resume(**values)
                self.assertEqual(self.ledger.status(self.job), self.before)
        self.assertEqual(self.calls, 0)

    def test_global_paid_ambiguity_rejects_reopen(self):
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_stages SET state='OUTCOME_UNKNOWN' "
                       "WHERE job_id=? AND name='gemini_primary'", (self.job,))
        with self.assertRaises(V2Conflict):
            self.resume()


if __name__ == "__main__":
    unittest.main()
