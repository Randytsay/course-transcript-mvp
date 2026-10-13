"""Offline immutable review output, paired receipts and real Chirp timing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from tests import test_golden_v2_smoke_ledger as fixture
from app.pipeline.golden_v2_smoke_bundle import build_smoke_bundle, write_smoke_bundle
from app.pipeline.golden_v2_smoke_ledger import MODELS, SmokeConflict
from app.jobs.golden_v2_ledger import V2Conflict


class SmokeReviewBundleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.OneClipPaidSmokeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.init()
        self.trial = self.fixture.ledger
        self.output = self.fixture.root.resolve() / "review-output"

    def complete(self, *, primary="如是我聞", third="如是我文", end=2):
        for model in MODELS:
            record = {"model": model, "clip_sha256": self.trial._scope()["clip_sha256"]}
            if model == "chirp_3":
                record.update({"timing_authority": "CHIRP_3", "words": [
                    {"word": "如是", "start_seconds": 0, "end_seconds": 1},
                    {"word": "我聞", "start_seconds": 1, "end_seconds": end}]})
            else:
                record.update({"text": primary if model == MODELS[0] else third,
                               "finish_reason": "STOP"})
            self.fixture.run_one(model=model, provider=lambda *_: record)

    def test_pending_has_no_output_and_no_claim(self):
        with self.assertRaisesRegex(SmokeConflict, "completed"):
            write_smoke_bundle(trial=self.trial, output_dir=self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.trial.status()["provider_calls_started"])

    def test_three_completed_receipts_emit_same_srt_txt_and_hash_manifest(self):
        self.complete()
        manifest = write_smoke_bundle(trial=self.trial, output_dir=self.output)
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((self.output / "candidate.chirp.txt").read_text(), "如是我聞\n")
        self.assertIn("00:00:00,000 --> 00:00:02,000\n如是我聞",
                      (self.output / "candidate.chirp.srt").read_text())
        bundle = json.loads((self.output / "comparison.json").read_text())
        self.assertFalse(bundle["golden_pass"])
        self.assertFalse(bundle["publication_allowed"])
        self.assertEqual(bundle["usage_estimate_total_cents"], 75)
        decision = bundle["review"]["decisions"][0]
        self.assertEqual(decision["clip_time_start_ms"], 1000)
        self.assertEqual(decision["parent_time_start_ms"], 601000)
        self.assertEqual(write_smoke_bundle(trial=self.trial, output_dir=self.output), manifest)

    def test_identical_models_still_need_full_audio_review(self):
        self.complete(third="如是我聞")
        report = build_smoke_bundle(trial=self.trial)
        self.assertEqual(report["review"]["decisions"], [])
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertFalse(report["review"]["full_clip_listened"])

    def test_changed_receipt_blocks_export(self):
        self.complete()
        next(self.trial.receipt_root.glob("*.json")).write_text("{}")
        with self.assertRaisesRegex(SmokeConflict, "checksum"):
            write_smoke_bundle(trial=self.trial, output_dir=self.output)
        self.assertFalse(self.output.exists())

    def test_timing_beyond_excerpt_is_blocked(self):
        self.complete(end=241)
        with self.assertRaises(SmokeConflict):
            build_smoke_bundle(trial=self.trial)

    def test_html_escapes_provider_text_and_untimed_insertions_stay_null(self):
        self.complete(primary="如是我聞<script>alert(1)</script>")
        write_smoke_bundle(trial=self.trial, output_dir=self.output)
        page = (self.output / "review.html").read_text()
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        bundle = json.loads((self.output / "comparison.json").read_text())
        self.assertIsNone(bundle["review"]["decisions"][0]["parent_time_start_ms"])

    def test_previous_output_never_overwritten(self):
        self.complete()
        write_smoke_bundle(trial=self.trial, output_dir=self.output)
        path = self.output / "primary.txt"
        path.write_text("human-edited")
        with self.assertRaisesRegex(SmokeConflict, "immutable"):
            write_smoke_bundle(trial=self.trial, output_dir=self.output)
        self.assertEqual(path.read_text(), "human-edited")

    def test_symlink_output_cannot_escape(self):
        self.complete()
        outside = self.fixture.root / "outside"
        outside.mkdir()
        self.output.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SmokeConflict, "linked"):
            write_smoke_bundle(trial=self.trial, output_dir=self.output)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
