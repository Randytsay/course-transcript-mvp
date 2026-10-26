"""Regression tests for bounded post-BASE Chirp repair reservations (no providers)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict


class ChirpRepairBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.ledger = GoldenV2Ledger(Path(self.temp.name) / "ledger.db")
        estimates = {
            "gemini_primary": 30, "chirp_independent": 20,
            "compare": 0, "third_optional": 10,
            "adjudication": 0, "golden_qa": 0,
        }
        self.job = self.ledger.create(
            source_sha="a" * 64, mode="SMART_2PLUS",
            estimated_stage_cents=estimates,
        )
        self.ledger.approve(
            self.job, revision=0, approved_cents=600,
            reference="owner-USD6-job-cap", actor="test-owner",
        )
        claim = self.ledger.claim_next(self.job, worker_id="test-primary")
        self.ledger.complete(claim, evidence_sha="f" * 64, actual_cents=8)
        self.claim = self.ledger.claim_next(self.job, worker_id="test-chirp")
        with self.ledger._tx() as db:
            db.execute(
                "INSERT INTO v2_chirp_operations "
                "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,"
                "status,request_id,actual_cents,estimated_cents,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.job, "chunk-0000", "a" * 64, "c" * 64,
                 "gs://private-input/base.flac", 0, 90000,
                 "DONE", "base-request", 20, 20, "2026-10-27T00:00:00+00:00"),
            )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def window(cents=6):
        return {
            "window_id": "repair-r01-00", "source_sha": "a" * 64,
            "clip_sha": "d" * 64, "gcs_uri": "gs://private-input/repair.flac",
            "output_uri": "gs://private-output/repair/",
            "start_ms": 8000, "end_ms": 22000,
            "estimated_cents": cents, "assessment_sha": "b" * 64,
            "target_intervals": [{"start_ms": 10000, "end_ms": 20000}],
            "strategy": "STANDARD_BATCH",
        }

    def approve(self, cents=6, cap=6):
        return self.ledger.approve_chirp_repair_plan(
            self.job, expected_revision=self.ledger.status(self.job)["revision"],
            assessment_sha="b" * 64, windows=[self.window(cents)],
            approved_cents=cap, reference="owner-repair-r01",
            actor="test-owner",
        )

    def stage_estimate(self):
        return next(s["estimate_cents"] for s in self.ledger.status(self.job)["stages"]
                    if s["name"] == "chirp_independent")

    def test_stage_expands_only_when_owner_approved_plan_is_consumed(self):
        approval = self.approve()
        self.assertEqual(self.stage_estimate(), 20)  # approval alone never widens cap
        self.ledger.plan_chirp_repair_windows(
            self.claim, plan_sha=approval["plan_sha"],
            windows=[self.window()],
        )
        self.assertEqual(self.stage_estimate(), 26)
        rows = self.ledger.chirp_windows(self.job)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["status"], "PLANNED")
        self.ledger.begin_chirp_submit(self.claim, "repair-r01-00")
        with self.assertRaisesRegex(V2Conflict, "already used"):
            self.ledger.plan_chirp_repair_windows(
                self.claim, plan_sha=approval["plan_sha"],
                windows=[self.window()],
            )

    def test_stale_qa_cannot_reapprove_completed_repair(self):
        approval = self.approve()
        self.ledger.plan_chirp_repair_windows(
            self.claim, plan_sha=approval["plan_sha"], windows=[self.window()])
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_chirp_operations SET status='DONE',actual_cents=6 "
                       "WHERE job_id=? AND window_id='repair-r01-00'", (self.job,))
        new_window = {**self.window(), "window_id": "repair-r02-00"}
        with self.assertRaisesRegex(V2Conflict, "overlaps completed Standard Batch"):
            self.ledger.approve_chirp_repair_plan(
                self.job, expected_revision=self.ledger.status(self.job)["revision"],
                assessment_sha="b" * 64, windows=[new_window],
                approved_cents=6, reference="stale-acoustic-report",
                actor="test-owner",
            )
        self.assertEqual(len(self.ledger.chirp_windows(self.job)), 2)

    def test_cannot_reserve_beyond_job_approval(self):
        with self.assertRaisesRegex(V2Conflict, "preapproved budget"):
            self.approve(cents=574, cap=574)
        self.assertEqual(self.stage_estimate(), 20)
        self.assertEqual(len(self.ledger.chirp_windows(self.job)), 1)

    def test_cannot_approve_ceiling_above_remaining_job_budget(self):
        with self.assertRaisesRegex(V2Conflict, "preapproved budget"):
            self.approve(cents=6, cap=573)
        self.assertEqual(self.stage_estimate(), 20)


if __name__ == "__main__":
    unittest.main()
