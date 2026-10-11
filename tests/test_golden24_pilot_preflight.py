"""Golden24 fail-closed pilot gate regression tests (no Drive/provider calls)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.golden24.pilot_preflight import (
    milliseconds, parse_srt, report_gate, verify_snapshot,
)


class PilotGateTests(unittest.TestCase):
    def test_timestamp_validation(self):
        self.assertEqual(milliseconds("02:03:46,299"), 7426299)
        with self.assertRaises(ValueError):
            milliseconds("00:61:00,000")

    def test_srt_parser_catches_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            srt = Path(d) / "a.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n測試一。\n\n"
                "2\n00:00:01,500 --> 00:00:03,000\n測試二。\n",
                encoding="utf-8",
            )
            cues, errors = parse_srt(srt)
            self.assertEqual(len(cues), 2)
            self.assertTrue(any("Overlap" in e for e in errors))

    def test_historical_pass_with_missing_speech_fails_closed(self):
        qa = {
            "status": "PASS", "errors": [], "human_missing_count": 3,
            "source_missing_count": 0,
            "boundary_integrity": {
                "split_candidate_count": 9,
                "close_unpunctuated_boundary_count": 33,
            },
        }
        blockers, review = report_gate({"qa": qa, "timing_truth": "chirp"})
        self.assertTrue(any("human_missing_count=3" in s for s in blockers))
        self.assertTrue(any("split_candidate_count=9" in s for s in review))

    def test_missing_counts_cannot_pass(self):
        blockers, _ = report_gate({"qa": {"status": "PASS"}, "timing_truth": "chirp"})
        self.assertTrue(blockers)

    def test_bad_manifest_never_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "SHA256SUMS.original.txt").write_text("invalid\n")
            with self.assertRaises(ValueError):
                verify_snapshot(p)

    def test_manifest_tampering_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            files = [
                "20260927.MP3", "20260927.srt", "20260927_逐字稿.txt",
                "20260927_逐字稿.reference-original-70116239.txt",
                "20260927_transcript_report.json",
            ]
            rows = []
            for name in files:
                (p / name).write_bytes(b"test")
                rows.append(hashlib.sha256(b"test").hexdigest() + "  " + name)
            (p / "SHA256SUMS.original.txt").write_text("\n".join(rows) + "\n")
            self.assertTrue(verify_snapshot(p)["verified"])
            (p / "20260927.srt").write_bytes(b"tampered")
            result = verify_snapshot(p)
            self.assertFalse(result["verified"])
            self.assertEqual(result["mismatched"], ["20260927.srt"])


if __name__ == "__main__":
    unittest.main()
