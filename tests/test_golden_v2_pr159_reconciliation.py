"""Provider-free regression: settle existing Chirp usage, never replay an LRO."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict


class CompletedChirpReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.ledger = GoldenV2Ledger(Path(self.temp.name) / "ledger.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def completed_over_estimate(self, approved_cents=600, cost_cents=136):
        job = self.ledger.create(
            source_sha="a" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 1, "chirp_independent": 78,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(job, revision=0, approved_cents=approved_cents,
                            reference="owner-test", actor="owner-test")
        gemini = self.ledger.claim_next(job, worker_id="test")
        self.ledger.complete(gemini, evidence_sha="e" * 64, actual_cents=1)
        claim = self.ledger.claim_next(job, worker_id="test")
        window = {
            "window_id": "chunk-000", "source_sha": "a" * 64,
            "clip_sha": "b" * 64, "gcs_uri": "gs://private-test/audio.flac",
            "start_ms": 0, "end_ms": 15000,
        }
        self.ledger.plan_chirp_window(claim, window)
        self.ledger.begin_chirp_submit(claim, "chunk-000")
        self.ledger.record_chirp_submission(
            claim, "chunk-000", "operations/known-complete")
        self.ledger.recover_submitted_chirp(
            job, "chunk-000", operation_name="operations/known-complete",
            evidence_sha="d" * 64, actual_cents=cost_cents,
            verifier=lambda **r: r["job_id"] == job
            and r["clip_sha"] == "b" * 64,
        )
        self.assertEqual(self.ledger.chirp_windows(job)[0]["status"], "DONE")
        self.assertEqual(self.ledger.status(job)["stages"][1]["state"], "OVER_BUDGET")
        return job

    def attest(self, job, **kwargs):
        self.ledger.finish_recovered_chirp(
            job, evidence_sha="f" * 64,
            verifier=lambda **r: r["job_id"] == job
            and r["source_sha"] == "a" * 64
            and len(r["windows"]) == 1
            and r["windows"][0]["result_sha"] == "d" * 64,
            **kwargs,
        )

    def test_136_cents_reconciled_inside_600_cents_but_review_not_gold(self):
        job = self.completed_over_estimate(approved_cents=600, cost_cents=136)
        self.attest(job, acoustic_review_required=True)
        state = self.ledger.status(job)
        self.assertEqual(state["status"], "NEEDS_REVIEW")
        self.assertEqual(state["stages"][1]["state"], "REVIEW_REQUIRED")
        self.assertEqual(state["stages"][1]["estimate_cents"], 78)
        self.assertEqual(state["stages"][1]["actual_cents"], 136)
        self.assertEqual(state["stages"][1]["review_required"], 1)
        self.assertEqual(state["spent_cents"], 137)
        with self.assertRaisesRegex(V2Conflict, "job not authorized/processing"):
            self.ledger.claim_next(job, worker_id="must-not-compare")
        with self.assertRaises(V2Conflict):
            self.attest(job, acoustic_review_required=False)

        other = self.ledger.create(
            source_sha="c" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 1, "chirp_independent": 1,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(other, revision=0, approved_cents=2,
                            reference="other-owner-test", actor="owner-test")
        self.assertEqual(self.ledger.claim_next(
            other, worker_id="separate-job",
            aggregate_cap_cents=900)["stage"], "gemini_primary")

    def test_above_owner_approval_remains_frozen_and_blocks_dispatch(self):
        job = self.completed_over_estimate(approved_cents=100, cost_cents=136)
        with self.assertRaisesRegex(V2Conflict, "exceed owner job approval"):
            self.attest(job, acoustic_review_required=True)
        state = self.ledger.status(job)
        self.assertEqual(state["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(state["stages"][1]["state"], "OVER_BUDGET")
        self.assertEqual(state["chirp_known_actual_cents"], 136)

    def test_early_review_reentry_is_cas_evidence_and_budget_bound(self):
        job = self.completed_over_estimate(approved_cents=600, cost_cents=136)
        self.attest(job, acoustic_review_required=True)
        state = self.ledger.status(job)
        options = dict(
            revision=state["revision"], source_sha="a" * 64,
            previous_chirp_evidence_sha="f" * 64,
            previous_acoustic_qa_sha="c" * 64,
            transfer_cents=64, reviewer="authenticated-owner",
            review_reference="approved-source-bound-review-only",
            worker_id="operator-local", aggregate_cap_cents=1000,
            evidence_verifier=lambda **r: (
                r["job_id"] == job and r["source_sha"] == "a" * 64
                and r["old_chirp_sha"] == "f" * 64
                and r["old_acoustic_sha"] == "c" * 64
                and len(r["windows"]) == 1
            ),
        )
        # The independent source / raw verifier and stage CAS fail closed.
        for altered in (
            {"source_sha": "b" * 64},
            {"revision": state["revision"] + 1},
            {"previous_chirp_evidence_sha": "a" * 64},
            {"transfer_cents": 40},
            {"evidence_verifier": lambda **_: False},
        ):
            with self.assertRaises(V2Conflict):
                self.ledger.resume_early_chirp_acoustic_review(
                    job, **{**options, **altered})
        self.assertEqual(self.ledger.status(job)["status"], "NEEDS_REVIEW")
        claimed = self.ledger.resume_early_chirp_acoustic_review(job, **options)
        self.assertEqual(claimed["stage"], "chirp_independent")
        self.assertEqual(claimed["provider_submissions"], 0)
        resumed = self.ledger.status(job)
        self.assertEqual(resumed["revision"], state["revision"] + 1)
        self.assertEqual(resumed["stages"][1]["state"], "RUNNING")
        self.assertEqual(resumed["stages"][1]["estimate_cents"], 142)
        self.assertEqual(resumed["stages"][1]["actual_cents"], 136)
        self.assertEqual(resumed["stages"][0]["state"], "COMPLETE")
        self.assertEqual(self.ledger.prior_chirp_merge_for_active_repair(job), "f" * 64)
        self.assertEqual(len(self.ledger.chirp_windows(job)), 1)
        with self.assertRaisesRegex(V2Conflict, "revision/state/evidence mismatch"):
            self.ledger.resume_early_chirp_acoustic_review(job, **options)

    def test_early_reentry_remains_frozen_when_unverified_provider_or_cap(self):
        job = self.completed_over_estimate(approved_cents=600, cost_cents=136)
        self.attest(job, acoustic_review_required=True)
        state = self.ledger.status(job)
        values = dict(
            revision=state["revision"], source_sha="a" * 64,
            previous_chirp_evidence_sha="f" * 64,
            previous_acoustic_qa_sha="c" * 64,
            transfer_cents=64, reviewer="authenticated-owner",
            review_reference="review-only", worker_id="operator",
            aggregate_cap_cents=140,
            evidence_verifier=lambda **_: True,
        )
        with self.assertRaisesRegex(V2Conflict, "reopened Chirp exposure exceeds aggregate cap"):
            self.ledger.resume_early_chirp_acoustic_review(job, **values)
        self.assertEqual(self.ledger.status(job)["status"], "NEEDS_REVIEW")
        self.assertEqual(self.ledger.status(job)["stages"][1]["state"], "REVIEW_REQUIRED")
        self.assertEqual(self.ledger.status(job)["stages"][0]["estimate_cents"], 1)
        self.assertEqual(self.ledger.chirp_windows(job)[0]["status"], "DONE")

    def test_pass_settles_sunk_charges_without_overwriting_estimate(self):
        job = self.completed_over_estimate(approved_cents=600, cost_cents=136)
        self.attest(job, acoustic_review_required=False)
        state = self.ledger.status(job)
        self.assertEqual(state["status"], "PROCESSING")
        self.assertEqual(state["stages"][1]["state"], "COMPLETE")
        self.assertEqual(state["stages"][1]["estimate_cents"], 78)
        self.assertEqual(state["stages"][1]["actual_cents"], 136)
        self.assertEqual(self.ledger.claim_next(
            job, worker_id="compare-no-new-provider")["stage"], "compare")


if __name__ == "__main__":
    unittest.main()
