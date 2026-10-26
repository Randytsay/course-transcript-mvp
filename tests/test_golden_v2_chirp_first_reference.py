"""Provider-free tests for the bounded and auditable 2022 manual SRT input."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_chirp_first_reference import read_chirp_first_reference


class ChirpFirstReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        v2 = self.root / "golden-v2"
        v2.mkdir()
        self.audio = self.root / "normalized.flac"
        self.audio.write_bytes(b"source-bytes-for-test-only")
        self.sha = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        self.srt_path = v2 / "chirp-first-reference.srt"
        self.meta_path = v2 / "chirp-first-reference.json"
        self.srt_path.write_text(
            "1\n00:00:00,100 --> 00:00:02,000\n復次\n\n"
            "2\n00:00:02,000 --> 00:00:07,950\n文殊師利\n",
            encoding="utf-8")
        self.packet = {
            "schema": "golden-v2-chirp-first-reference-v1",
            "job_id": "v2p_" + "a" * 32, "source_sha256": self.sha,
            "reference_srt_sha256": hashlib.sha256(
                self.srt_path.read_bytes()).hexdigest(),
            "reviewer": "test-reviewed-owner",
            "review_reference": "local-fixture-with-original-hash",
            "source_kind": "HUMAN_CORRECTED_SRT",
        }
        self.persist()

    def tearDown(self):
        self.temp.cleanup()

    def persist(self):
        self.meta_path.write_text(json.dumps(self.packet), encoding="utf-8")

    def read(self):
        return read_chirp_first_reference(
            job_dir=self.root, job_id=self.packet["job_id"],
            source_sha=self.sha, duration_ms=8000)

    def test_full_extent_source_bound_manual_reference(self):
        text, meta = self.read()
        self.assertEqual(text, "復次文殊師利")
        self.assertEqual(meta["reference_kind"], "HUMAN_CORRECTED_SRT")
        self.assertFalse(meta["reference_is_acoustic_evidence"])
        self.assertGreater(meta["reference_timeline_extent_ratio"], 0.98)

    def test_changed_srt_rejected_even_if_syntactically_valid(self):
        self.srt_path.write_bytes(self.srt_path.read_bytes().replace(
            "文殊師利".encode(), "文殊師梨".encode()))
        with self.assertRaisesRegex(V2Conflict, "source-bound operator"):
            self.read()

    def test_wrong_source_sha_rejected(self):
        self.packet["source_sha256"] = "f" * 64
        self.persist()
        with self.assertRaises(V2Conflict):
            self.read()

    def test_truncated_manual_srt_cannot_pass_as_full_reference(self):
        self.srt_path.write_text(
            "1\n00:00:00,100 --> 00:00:01,000\n復次\n",
            encoding="utf-8")
        self.packet["reference_srt_sha256"] = hashlib.sha256(
            self.srt_path.read_bytes()).hexdigest()
        self.persist()
        with self.assertRaisesRegex(V2Conflict, "full source timeline"):
            self.read()

    def test_symlinked_reference_rejected(self):
        self.srt_path.unlink()
        self.srt_path.symlink_to(self.audio)
        with self.assertRaisesRegex(V2Conflict, "symlink"):
            self.read()

    def test_forged_provider_identity_rejected(self):
        self.packet["source_kind"] = "GEMINI_PRIMARY"
        self.persist()
        with self.assertRaises(V2Conflict):
            self.read()


if __name__ == "__main__":
    unittest.main()
