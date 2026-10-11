"""V2 source-scoped stage ledger persistence without any provider calls."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_stage_contract import V2Selection
from app.jobs.golden_v2_store import GoldenV2StageStore

SHA = "a" * 64
OTHER = "b" * 64


class GoldenV2DurableLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "golden-v2.db"
        self.store = GoldenV2StageStore(self.path)
        self.store.initialize()
        self.job_id = "job-test-001"
        self.store.create_job(self.job_id, V2Selection(SHA))

    def record(self, stage, *, evidence=SHA, gate=None):
        return self.store.record_stage(
            self.job_id, stage=stage, evidence_sha256=evidence, gate=gate
        )

    def test_stage_order_requires_primary_first(self):
        self.assertEqual(self.store.status(self.job_id)["next_stage"], "inventory")
        with self.assertRaises(ValueError):
            self.record("chirp_independent")
        self.assertTrue(self.record("inventory"))
        self.assertTrue(self.record("preflight"))
        self.assertTrue(self.record("gemini_primary"))
        self.assertTrue(self.record("gemini_primary_completeness", gate={
            "primary_candidate_complete": True,
        }))
        self.assertEqual(self.store.status(self.job_id)["next_stage"], "chirp_independent")
        self.assertTrue(self.record("chirp_independent"))

    def test_restart_uses_persisted_completed_evidence(self):
        self.record("inventory")
        reloaded = GoldenV2StageStore(self.path)
        self.assertEqual(reloaded.status(self.job_id)["completed_stages"], ["inventory"])
        self.assertFalse(reloaded.create_job(self.job_id, V2Selection(SHA)))
        self.assertFalse(reloaded.record_stage(
            self.job_id, stage="inventory", evidence_sha256=SHA,
        ))
        self.assertTrue(reloaded.record_stage(
            self.job_id, stage="preflight", evidence_sha256=OTHER,
        ))

    def test_conflicting_provider_evidence_cannot_replace_existing(self):
        self.record("inventory")
        with self.assertRaises(ValueError):
            self.record("inventory", evidence=OTHER)
        self.assertEqual(self.store.status(self.job_id)["last_evidence_sha256"], SHA)

    def test_source_or_mode_change_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_job(self.job_id, V2Selection(OTHER))
        with self.assertRaises(ValueError):
            self.store.create_job(self.job_id, V2Selection(SHA, mode="SMART_2PLUS"))

    def test_failed_primary_gate_prevents_chirp_submission(self):
        self.record("inventory")
        self.record("preflight")
        self.record("gemini_primary")
        self.record("gemini_primary_completeness", gate={
            "primary_candidate_complete": False
        })
        with self.assertRaises(ValueError):
            self.record("chirp_independent")

    def test_private_text_and_arbitrary_metadata_are_rejected(self):
        with self.assertRaises(ValueError):
            self.record("inventory", gate={"transcript_raw": "不得寫入 SQLite"})
        with self.assertRaises(ValueError):
            self.record("inventory", gate={"acoustic_gate_pass": "yes"})


if __name__ == "__main__":
    unittest.main()
