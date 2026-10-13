"""Provider-free end-to-end acceptance trial including no-retry failure holds."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
import uuid

from app.pipeline.golden_v2_live_smoke_plan import (
    build_paid_smoke_plan, persist_plan_once,
)
from app.pipeline.golden_v2_smoke_ledger import (
    SingleClipSmokeLedger, SmokeConflict, MODELS,
)


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class OneClipPaidSmokeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.media = self.root / "original.mp3"
        self.excerpt = self.root / "excerpt.flac"
        self.preflight = self.root / "source-preflight.json"
        self.offline = self.root / "offline.json"
        self.plan = self.root / "approved-smoke-scope-pending-cloud.json"
        self.media.write_bytes(b"original Google Drive source bytes")
        self.excerpt.write_bytes(b"derived 4min clip not Drive original")
        observation = {"provider": "google_drive", "file_id": "test-parent-17",
                       "version": "33"}
        metadata = {
            "schema": "golden-v2-source-preflight-v1",
            "original_sha256": sha(self.media.read_bytes()),
            "source_sha256": sha(b"normalized audio"),
            "original_md5_checksum": "test-parent-md5",
            "drive_observation": observation,
        }
        self.preflight.write_text(json.dumps(metadata))
        uid = uuid.uuid5(uuid.NAMESPACE_URL,
                         "golden-v2-source-preflight:" +
                         sha(self.preflight.read_bytes()))
        offline = {
            "schema": "golden-v2-real-audio-offline-smoke-v1",
            "source": {
                "sha256": sha(self.media.read_bytes()),
                "drive_file_id": "test-parent-17", "drive_version": "33",
                "drive_md5": "test-parent-md5",
            },
            "excerpt": {
                "sha256": sha(self.excerpt.read_bytes()),
                "derived_from_source_sha256": sha(self.media.read_bytes()),
                "is_independent_drive_source": False, "start_seconds": 600,
                "end_seconds": 840, "duration_seconds": 240.0,
                "size_bytes": self.excerpt.stat().st_size,
            },
            "preflight": {
                "job_id": "v2p_" + uid.hex,
                "status": "AWAITING_APPROVAL",
                "source_sha256": sha(b"normalized audio"),
            },
        }
        self.offline.write_text(json.dumps(offline))
        scope = build_paid_smoke_plan(
            offline_manifest=self.offline,
            original_preflight=self.preflight, original_media=self.media,
            excerpt_audio=self.excerpt,
        )
        persist_plan_once(self.plan, scope)
        self.ledger = SingleClipSmokeLedger(
            db=self.root / "clip-trial.sqlite",
            plan_path=self.plan, offline_manifest=self.offline,
            source_preflight=self.preflight, original_media=self.media,
            excerpt_audio=self.excerpt, receipt_root=self.root / "receipts",
        )

    def init(self):
        return self.ledger.initialize(
            owner_reference="user-chat-2026-10-13-one-clip-USD10",
        )

    def run_one(self, *, model=MODELS[0], provider=None,
                accountant=None, owner=None, capability=None):
        return self.ledger.execute_one(
            model=model,
            provider_call=provider or (
                lambda path, requested: {
                    "model_version": requested,
                    "text": "fake: audible test only",
                    "source_bytes": path.stat().st_size,
                }
            ),
            cost_accountant=accountant or (lambda response: 25),
            verify_owner=owner or (lambda _: True),
            verify_capability_and_pricing=capability or (
                lambda _, model: True
            ),
        )

    def test_initialize_fails_duplicate_and_remains_pending(self):
        state = self.init()
        self.assertEqual(state["cap_cents"], 1000)
        self.assertFalse(state["provider_calls_started"])
        self.assertEqual([x["state"] for x in state["stages"]],
                         ["PENDING", "PENDING", "PENDING"])
        with self.assertRaises(SmokeConflict):
            self.init()

    def test_fake_provider_all_three_ordered_one_time(self):
        self.init()
        with self.assertRaisesRegex(SmokeConflict, "prior stage unresolved"):
            self.run_one(model=MODELS[1])
        for model in MODELS:
            result = self.run_one(model=model)
            self.assertEqual(result["model"], model)
            self.assertEqual(result["actual_cents"], 25)
            self.assertEqual(result["state"], "COMPLETE")
        state = self.ledger.status()
        self.assertEqual([x["state"] for x in state["stages"]],
                         ["COMPLETE"] * 3)
        self.assertEqual(len(list((self.root / "receipts").glob("*.json"))), 3)
        with self.assertRaisesRegex(SmokeConflict, "already charged"):
            self.run_one(model=MODELS[0])

    def test_missing_owner_verification_never_claims(self):
        self.init()
        with self.assertRaisesRegex(SmokeConflict, "trusted owner"):
            self.run_one(owner=lambda _: False)
        self.assertFalse(self.ledger.status()["provider_calls_started"])

    def test_missing_model_pricing_verification_never_claims(self):
        self.init()
        with self.assertRaisesRegex(SmokeConflict, "trusted owner"):
            self.run_one(capability=lambda _plan, _model: False)
        self.assertFalse(self.ledger.status()["provider_calls_started"])

    def test_provider_timeout_holds_and_never_retries(self):
        self.init()
        attempts = []
        def fake_timeout(_path, _model):
            attempts.append("sent")
            raise TimeoutError("provider may have billed")
        with self.assertRaises(TimeoutError):
            self.run_one(provider=fake_timeout)
        self.assertEqual(attempts, ["sent"])
        state = self.ledger.status()
        self.assertEqual(state["stages"][0]["state"], "OUTCOME_UNKNOWN")
        with self.assertRaisesRegex(SmokeConflict, "already charged"):
            self.run_one(provider=fake_timeout)
        self.assertEqual(attempts, ["sent"])

    def test_cost_unknown_blocks_later_stage(self):
        self.init()
        with self.assertRaisesRegex(SmokeConflict, "unknown cost"):
            self.run_one(accountant=lambda _: None)
        with self.assertRaisesRegex(SmokeConflict, "prior stage unresolved"):
            self.run_one(model=MODELS[1])
        self.assertEqual(self.ledger.status()["stages"][0]["state"],
                         "OUTCOME_UNKNOWN")

    def test_actual_over_budget_blocks_subsequent_stage(self):
        self.init()
        with self.assertRaisesRegex(SmokeConflict, "actual billed"):
            self.run_one(accountant=lambda _: 201)
        self.assertEqual(self.ledger.status()["stages"][0]["state"],
                         "OVER_BUDGET")
        with self.assertRaises(SmokeConflict):
            self.run_one(model=MODELS[1])

    def test_source_mutation_blocks_before_call(self):
        self.init()
        self.excerpt.write_bytes(b"tampered derived clip")
        sent = []
        with self.assertRaises(SmokeConflict):
            self.run_one(provider=lambda *_: sent.append(True))
        self.assertEqual(sent, [])
        self.assertFalse(self.ledger.status()["provider_calls_started"])

    def test_concurrent_claim_has_one_provider_call(self):
        self.init()
        barrier = threading.Barrier(2)
        sent = []
        outcomes = []
        def run():
            barrier.wait(timeout=5)
            try:
                self.run_one(provider=lambda *_: (
                    sent.append(1) or {"model_version": MODELS[0],
                                       "text": "fake response"}
                ))
                outcomes.append("COMPLETE")
            except SmokeConflict:
                outcomes.append("BLOCKED")
        workers = [threading.Thread(target=run) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=8)
        self.assertEqual(sorted(outcomes), ["BLOCKED", "COMPLETE"])
        self.assertEqual(sent, [1])


if __name__ == "__main__":
    unittest.main()