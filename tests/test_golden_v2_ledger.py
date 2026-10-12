"""Provider-free Golden V2 SQLite restart, cost and stage-order tests."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict


def estimates():
    # Invented TEST cents, not provider prices or a billing authorization.
    return {"gemini_primary": 30, "chirp_independent": 20, "compare": 0,
            "third_optional": 10, "adjudication": 0, "golden_qa": 0}


def window():
    return {"index": 0, "start_seconds": 0, "end_seconds": 90,
            "gcs_uri": "gs://test-private/window.wav", "mime_type": "audio/wav"}


class GoldenV2LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "ledger.db"
        self.store = GoldenV2Ledger(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create(self, mode="QUALITY_3WAY"):
        return self.store.create(source_sha="a"*64, mode=mode,
                                 estimated_stage_cents=estimates())

    def approve(self, job):
        self.store.approve(job, revision=0, approved_cents=60,
                           reference="owner-reviewed-test", actor="test-owner")

    def done(self, claim, dispute=None):
        self.store.complete(claim, evidence_sha="e"*64, actual_cents=0,
                            comparison_has_dispute=dispute)

    def test_prior_approved_source_requires_explicit_repeat_confirmation(self):
        first = self.create()
        second = self.create()
        self.assertNotEqual(first, second)
        self.store.approve(first, revision=0, approved_cents=60,
                           reference="first-source-approval", actor="test-owner")
        with self.assertRaisesRegex(V2Conflict, "repeat_source_confirmed"):
            self.store.approve(second, revision=0, approved_cents=60,
                               reference="second-source-approval", actor="test-owner")
        self.assertEqual(self.store.status(second)["status"], "AWAITING_APPROVAL")
        self.store.approve(second, revision=0, approved_cents=60,
                           reference="explicit-reprocessing-reviewed", actor="test-owner",
                           repeat_source_confirmed=True)
        self.assertEqual(self.store.status(second)["status"], "QUEUED")

    def test_no_claim_before_reviewed_approval(self):
        job = self.create()
        with self.assertRaises(V2Conflict):
            self.store.claim_next(job, worker_id="worker-A")
        with self.assertRaises(V2Conflict):
            self.store.approve(job, revision=100, approved_cents=60,
                               reference="test", actor="test")
        with self.assertRaises(V2Conflict):
            self.store.approve(job, revision=0, approved_cents=59,
                               reference="test", actor="test")
        self.assertEqual(self.store.status(job)["status"], "AWAITING_APPROVAL")

    def test_expected_stage_refuses_to_claim_a_paid_stage(self):
        job = self.create()
        self.approve(job)
        with self.assertRaisesRegex(V2Conflict, "expected adjudication"):
            self.store.claim_next(job, worker_id="local-review",
                                  expected_stage="adjudication")
        state = self.store.status(job)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["stages"][0]["state"], "PENDING")
        self.assertEqual(state["status"], "QUEUED")

    def test_claim_is_not_reclaimed_on_restart_and_unknown_is_blocker(self):
        job = self.create()
        self.approve(job)
        claim = self.store.claim_next(job, worker_id="A")
        self.assertEqual(claim["stage"], "gemini_primary")
        recovered = GoldenV2Ledger(self.db)
        with self.assertRaises(V2Conflict):
            recovered.claim_next(job, worker_id="B")
        recovered.mark_outcome_unknown(claim)
        with self.assertRaises(V2Conflict):
            recovered.claim_next(job, worker_id="B")
        self.assertEqual(recovered.status(job)["status"], "NEEDS_RECONCILIATION")

    def test_paid_adapter_requires_matching_claim_source_model_and_window(self):
        job = self.create()
        self.approve(job)
        claim = self.store.claim_next(job, worker_id="A")
        validate = self.store.provider_approval_validator(claim)
        self.assertFalse(validate(claim["claim"], "a"*64, "gemini-3.8-flash", window()))
        self.store.register_window(claim, window())
        self.assertTrue(validate(claim["claim"], "a"*64, "gemini-3.8-flash", window()))
        self.assertFalse(validate("fake", "a"*64, "gemini-3.8-flash", window()))
        self.assertFalse(validate(claim["claim"], "b"*64, "gemini-3.8-flash", window()))
        self.assertFalse(validate(claim["claim"], "a"*64, "gemini-3.5-transcribe", window()))
        self.assertFalse(validate(claim["claim"], "a"*64, "gemini-3.8-flash",
                                  {**window(), "gcs_uri": "gs://test-private/other.wav"}))
        self.done(claim)
        self.assertFalse(validate(claim["claim"], "a"*64, "gemini-3.8-flash", window()))

    def test_quality_mode_exact_order_and_no_automatic_publish(self):
        job = self.create()
        self.approve(job)
        names = []
        for name in STAGES:
            claim = self.store.claim_next(job, worker_id="A")
            names.append(claim["stage"])
            self.done(claim, dispute=True if name == "compare" else None)
        self.assertEqual(tuple(names), STAGES)
        with self.assertRaises(V2Conflict):
            self.store.claim_next(job, worker_id="A")
        self.assertEqual(self.store.status(job)["status"], "AWAITING_FINAL_REVIEW")

    def test_smart_without_disagreement_skips_third_and_never_bills(self):
        job = self.create(mode="SMART_2PLUS")
        self.approve(job)
        for name in ("gemini_primary", "chirp_independent", "compare"):
            claim = self.store.claim_next(job, worker_id="A")
            self.assertEqual(claim["stage"], name)
            self.done(claim, dispute=False if name == "compare" else None)
        claim = self.store.claim_next(job, worker_id="A")
        self.assertEqual(claim["stage"], "adjudication")
        status = self.store.status(job)
        self.assertEqual(next(r["state"] for r in status["stages"] if r["name"] == "third_optional"), "SKIPPED")

    def test_unknown_provider_reconciles_from_verified_evidence_without_rerun(self):
        job = self.create()
        self.approve(job)
        claim = self.store.claim_next(job, worker_id="A")
        self.store.mark_outcome_unknown(claim)
        recovered = GoldenV2Ledger(self.db)
        with self.assertRaises(V2Conflict):
            recovered.reconcile_unknown(
                job, stage="gemini_primary", actor="reviewer",
                evidence_sha="d"*64, actual_cents=9,
                evidence_verifier=lambda **e: False,
            )
        self.assertEqual(recovered.status(job)["status"], "NEEDS_RECONCILIATION")
        verification = []
        recovered.reconcile_unknown(
            job, stage="gemini_primary", actor="reviewer",
            evidence_sha="d"*64, actual_cents=9,
            evidence_verifier=lambda **e: verification.append(e) or (
                e["source_sha"] == "a"*64 and e["stage"] == "gemini_primary"
            ),
        )
        self.assertEqual(len(verification), 1)
        self.assertEqual(recovered.status(job)["spent_cents"], 9)
        self.assertEqual(recovered.claim_next(job, worker_id="B")["stage"], "chirp_independent")
        with self.assertRaises(V2Conflict):
            recovered.reconcile_unknown(
                job, stage="gemini_primary", actor="reviewer",
                evidence_sha="d"*64, actual_cents=9,
                evidence_verifier=lambda **e: True,
            )

    def test_cost_ledger_preserves_actual_above_reserved_and_blocks_replay(self):
        job = self.create()
        self.approve(job)
        claim = self.store.claim_next(job, worker_id="A")
        self.store.complete(claim, evidence_sha="b"*64, actual_cents=31)
        reopened = GoldenV2Ledger(self.db)
        status = reopened.status(job)
        self.assertEqual(status["stages"][0]["state"], "OVER_BUDGET")
        self.assertEqual(status["stages"][0]["evidence_sha"], "b"*64)
        self.assertEqual(status["spent_cents"], 31)
        self.assertEqual(status["status"], "NEEDS_RECONCILIATION")
        with self.assertRaises(V2Conflict):
            reopened.claim_next(job, worker_id="B")
        with self.assertRaises(V2Conflict):
            reopened.complete(claim, evidence_sha="b"*64, actual_cents=31)

    def test_verified_unknown_overrun_is_recorded_instead_of_lost(self):
        job = self.create()
        self.approve(job)
        claim = self.store.claim_next(job, worker_id="A")
        self.store.mark_outcome_unknown(claim, known_cents=12, evidence_sha="a"*64)
        self.store.reconcile_unknown(
            job, stage="gemini_primary", actor="reviewer",
            evidence_sha="c"*64, actual_cents=34,
            evidence_verifier=lambda **_: True,
        )
        status = GoldenV2Ledger(self.db).status(job)
        self.assertEqual(status["spent_cents"], 34)
        self.assertEqual(status["stages"][0]["evidence_sha"], "c"*64)
        self.assertEqual(status["stages"][0]["state"], "OVER_BUDGET")
        self.assertEqual(status["status"], "NEEDS_RECONCILIATION")
        with self.assertRaises(V2Conflict):
            self.store.claim_next(job, worker_id="C")

    def test_double_completion_does_not_charge_twice(self):
        job = self.create()
        self.approve(job)
        claim = self.store.claim_next(job, worker_id="A")
        self.store.complete(claim, evidence_sha="b"*64, actual_cents=12)
        with self.assertRaises(V2Conflict):
            self.store.complete(claim, evidence_sha="b"*64, actual_cents=12)
        self.assertEqual(self.store.status(job)["spent_cents"], 12)


if __name__ == "__main__":
    unittest.main()
