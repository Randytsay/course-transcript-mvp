"""Offline proof that a derived sample cannot impersonate original Drive media."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

from app.pipeline.golden_v2_live_smoke_plan import (
    build_paid_smoke_plan, persist_plan_once,
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class PaidSmokePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / "original.mp3"
        self.excerpt = self.root / "excerpt.flac"
        self.preflight = self.root / "source-preflight.json"
        self.offline = self.root / "offline.json"
        self.original.write_bytes(b"real original")
        self.excerpt.write_bytes(b"derived 4 minute clip")
        self.obs = {
            "file_id": "google-drive-123", "version": "33",
            "provider": "google_drive",
        }
        self.pre = {
            "schema": "golden-v2-source-preflight-v1",
            "original_sha256": sha(self.original.read_bytes()),
            "source_sha256": sha(b"normalized"),
            "original_md5_checksum": "test-md5",
            "drive_observation": self.obs,
        }
        self.preflight.write_text(json.dumps(self.pre))
        registration = sha(self.preflight.read_bytes())
        self.job = "v2p_" + uuid.uuid5(
            uuid.NAMESPACE_URL, "golden-v2-source-preflight:" + registration,
        ).hex
        self.bundle = {
            "schema": "golden-v2-real-audio-offline-smoke-v1",
            "source": {
                "sha256": sha(self.original.read_bytes()),
                "drive_file_id": self.obs["file_id"],
                "drive_version": "33", "drive_md5": "test-md5",
            },
            "excerpt": {
                "sha256": sha(self.excerpt.read_bytes()),
                "derived_from_source_sha256": sha(self.original.read_bytes()),
                "is_independent_drive_source": False,
                "start_seconds": 600, "end_seconds": 840,
                "duration_seconds": 240.0,
                "size_bytes": self.excerpt.stat().st_size,
            },
            "preflight": {
                "job_id": self.job, "source_sha256": sha(b"normalized"),
                "status": "AWAITING_APPROVAL",
            },
        }
        self.offline.write_text(json.dumps(self.bundle))

    def plan(self, **kwargs):
        return build_paid_smoke_plan(
            offline_manifest=self.offline,
            original_preflight=self.preflight,
            original_media=self.original,
            excerpt_audio=self.excerpt,
            **kwargs,
        )

    def test_exact_short_clip_cap_and_model_order(self):
        plan = self.plan()
        self.assertEqual(plan["cap_usd_cents"], 1000)
        self.assertEqual(plan["parent_v2_job_id"], self.job)
        self.assertEqual(plan["clip_sha256"], sha(self.excerpt.read_bytes()))
        self.assertFalse(plan["clip_is_independent_drive_source"])
        self.assertEqual(plan["models_in_order"], [
            "gemini-3.8-flash", "chirp_3", "gemini-3.5-transcribe",
        ])
        self.assertFalse(plan["paid_execution_enabled"])
        self.assertFalse(plan["provider_calls_started"])
        self.assertFalse(plan["approved_ledger_claim_exists"])
        self.assertEqual(
            sum(plan["stage_reservation_caps_usd_cents"].values()) +
            plan["unallocated_contingency_usd_cents"], 1000,
        )

    def test_malformed_source_or_clip_blocks(self):
        self.excerpt.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            self.plan()
        self.excerpt.write_bytes(b"derived 4 minute clip")
        self.bundle["excerpt"]["is_independent_drive_source"] = True
        self.offline.write_text(json.dumps(self.bundle))
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            self.plan()

    def test_changed_drive_version_or_forged_parent_identity_blocks(self):
        self.pre["drive_observation"]["version"] = "34"
        self.preflight.write_text(json.dumps(self.pre))
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            self.plan()

    def test_reject_higher_cap_and_impossible_multimodel_budget(self):
        with self.assertRaises(ValueError):
            self.plan(max_total_cents=1001)
        with self.assertRaises(ValueError):
            self.plan(max_total_cents=599)

    def test_persist_is_create_once_and_replay_readonly(self):
        plan = self.plan()
        output = self.root / "approved-scope.json"
        persist_plan_once(output, plan)
        previous = output.read_bytes()
        persist_plan_once(output, plan)
        self.assertEqual(output.read_bytes(), previous)
        with self.assertRaisesRegex(ValueError, "immutable smoke plan differs"):
            persist_plan_once(output, {**plan, "cap_usd_cents": 999})

    def test_source_symlink_cannot_escape_scope(self):
        source = self.root / "linked.mp3"
        source.symlink_to(self.original)
        with self.assertRaises(ValueError):
            build_paid_smoke_plan(
                offline_manifest=self.offline,
                original_preflight=self.preflight,
                original_media=source, excerpt_audio=self.excerpt,
            )


if __name__ == "__main__":
    unittest.main()