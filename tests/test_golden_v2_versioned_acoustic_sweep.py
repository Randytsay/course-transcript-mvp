"""Versioned evidence and complete acoustic sweep regression; no cloud calls."""
import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_versioned_artifacts import (
    resolve_versioned, version_path, write_versioned,
)
from app.providers.golden_v2_acoustic_qa import inspect_v2_acoustic


class VersionedArtifactTests(unittest.TestCase):
    def test_retains_original_and_resolves_by_exact_ledger_digest(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            canonical = root / "chirp-merged.json"
            sha0, p0 = write_versioned(canonical, b'{"first":1}')
            self.assertEqual(p0, canonical)
            sha1, p1 = write_versioned(canonical, b'{"second":2}')
            self.assertNotEqual(sha0, sha1)
            self.assertEqual(p1, version_path(canonical, sha1))
            self.assertEqual(canonical.read_bytes(), b'{"first":1}')
            self.assertEqual(resolve_versioned(canonical, sha0), canonical)
            self.assertEqual(resolve_versioned(canonical, sha1), p1)
            self.assertEqual(write_versioned(canonical, b'{"second":2}')[1], p1)
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_does_not_fallback_to_modified_or_untrusted_version(self):
        with TemporaryDirectory() as d:
            canonical = Path(d) / "golden-qa.json"
            first, _ = write_versioned(canonical, b"hold")
            second, version = write_versioned(canonical, b"pass")
            version.write_bytes(b"tampered")
            with self.assertRaises(V2Conflict):
                resolve_versioned(canonical, second)
            self.assertEqual(resolve_versioned(canonical, first), canonical)
            with self.assertRaises(V2Conflict):
                write_versioned(canonical, b"pass")

    def test_rejects_symlink_even_when_matching_content(self):
        with TemporaryDirectory() as d:
            canonical = Path(d) / "final.srt"
            elsewhere = Path(d) / "elsewhere"
            elsewhere.write_bytes(b"data")
            canonical.symlink_to(elsewhere)
            with self.assertRaises(V2Conflict):
                write_versioned(canonical, b"data")
            with self.assertRaises(V2Conflict):
                resolve_versioned(canonical, hashlib.sha256(b"data").hexdigest())


class AcousticSweepTests(unittest.TestCase):
    def setUp(self):
        self.d = TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.folder = Path(self.d.name)
        (self.folder / "normalized.flac").write_bytes(b"source-verified-flac")
        self.sha = hashlib.sha256(b"source-verified-flac").hexdigest()

    def sweep(self, words, duration_ms, *, non_speech_fn=None, **kwargs):
        merged = {
            "job_id": "test", "source_sha": self.sha,
            "duration_ms": duration_ms, "words": words,
            "timing_authority": "chirp_3",
        }
        def vad(_, start, end):
            if non_speech_fn is None or non_speech_fn(start, end):
                return {"robust_non_speech": True, "robust_speech": False}
            return {"robust_non_speech": False, "robust_speech": True}
        def volume(_, start, end):
            return -80.0 if non_speech_fn is None or non_speech_fn(start, end) else -15.0
        with patch("app.providers.golden_v2_acoustic_qa.duration_seconds",
                   return_value=duration_ms / 1000), patch(
                   "app.providers.golden_v2_acoustic_qa._vad_assessment", side_effect=vad), patch(
                   "app.providers.golden_v2_acoustic_qa._peak_db", side_effect=volume), patch(
                   "app.providers.golden_v2_acoustic_qa._audible", return_value=True):
            return inspect_v2_acoustic(job_dir=self.folder, merged=merged, **kwargs)

    def test_more_than_sixteen_gaps_all_inspected_and_silent(self):
        words = [{"word": "佛", "start_ms": i*4000, "end_ms": i*4000+500}
                 for i in range(26)]
        qa = self.sweep(words, 100500)
        self.assertEqual(qa["status"], "PASS")
        self.assertEqual(qa["unexamined_gap_count"], 0)
        self.assertEqual(qa["nonspeech_confirmed_count"], 25)
        self.assertEqual(qa["blocker_count"], 0)

    def test_long_gap_is_sliced_and_positive_speech_is_not_dismissed(self):
        words = [{"word": "法", "start_ms": 5000, "end_ms": 5400},
                 {"word": "會", "start_ms": 75000, "end_ms": 75400}]
        qa = self.sweep(
            words, 75400, max_gap_probe_ms=30000,
            non_speech_fn=lambda start, end: start < 30000,
        )
        self.assertEqual(qa["unexamined_gap_count"], 0)
        self.assertEqual(qa["status"], "REVIEW")
        self.assertEqual(qa["gaps_examined"][0]["disposition"], "NONSPEECH_CONFIRMED")
        second = qa["gaps_examined"][1]
        self.assertEqual(second["disposition"], "SPEECH_CONFIRMED")
        self.assertEqual(len(second["probe_chunks"]), 3)
        self.assertEqual(second["probe_chunks"][-1]["end_ms"], 74800)
        self.assertEqual(qa["repair_plan"]["intervals"][0]["target_intervals"][0]["gap_index"], 1)

    def test_uncertain_gap_does_not_pass(self):
        words = [{"word": "師", "start_ms": 100, "end_ms": 400},
                 {"word": "父", "start_ms": 6100, "end_ms": 6500}]
        merged = {"source_sha": self.sha, "duration_ms": 6500,
                  "timing_authority": "chirp_3", "words": words}
        with patch("app.providers.golden_v2_acoustic_qa.duration_seconds",
                   return_value=6.5), patch(
                   "app.providers.golden_v2_acoustic_qa._vad_assessment",
                   return_value={"robust_non_speech": False, "robust_speech": False}), patch(
                   "app.providers.golden_v2_acoustic_qa._peak_db", return_value=-20), patch(
                   "app.providers.golden_v2_acoustic_qa._audible", return_value=True):
            qa = inspect_v2_acoustic(job_dir=self.folder, merged=merged)
        self.assertEqual(qa["status"], "REVIEW")
        self.assertEqual(qa["uncertain_count"], 1)
        self.assertEqual(qa["repair_plan"]["intervals"], [])


if __name__ == "__main__":
    unittest.main()