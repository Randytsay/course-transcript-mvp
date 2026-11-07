"""Review interval manifest regression tests: all data are synthetic."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.golden_v2_offline_srt_timeline_qa import (
    build_review_interval_manifest, parse_srt, main,
)


def cue(i, start, end, text="字"):
    return f"{i}\n{start} --> {end}\n{text}\n"


def provider(source, complete, words):
    return {
        "schema": "golden-v2-source-bound-word-timing-evidence-v1",
        "source_sha256": source,
        "complete_source_word_inventory": complete,
        "words": words,
    }


class ReviewIntervalTests(unittest.TestCase):
    def test_isolated_word_inside_srt_gap_is_a_contradiction(self):
        data = (
            cue(1, "00:29:48,240", "00:29:49,920") + "\n"
            + cue(2, "00:32:53,440", "00:32:54,020") + "\n"
            + cue(3, "00:35:35,280", "00:35:36,000")
        )
        result = build_review_interval_manifest(
            parse_srt(data), provider_evidence=provider(
                "a" * 64, True, [
                    {"start_ms": 1_942_840, "end_ms": 1_943_000},
                ],
            ),
            expected_source_sha="a" * 64, audio_duration_ms=2_200_000,
        )
        a, b = result["intervals"]
        self.assertEqual(a["srt_gap_ms"], 183520)
        self.assertEqual(b["srt_gap_ms"], 161260)
        self.assertEqual(a["review_start"], "00:29:46,920")
        self.assertEqual(a["review_end"], "00:32:56,440")
        self.assertEqual(a["provider_word_anchor_count"], 1)
        self.assertEqual(a["provider_evidence_state"],
                         "PROVIDER_ANCHOR_WITHIN_SRT_GAP")
        self.assertEqual(b["provider_evidence_state"],
                         "EVIDENCE_MISSING_NO_PROVIDER_WORD_ANCHOR")
        self.assertEqual(result["total_srt_gap_ms"], 344780)
        self.assertFalse(result["interpolation_performed"])
        self.assertFalse(result["golden_qa_pass"])
        self.assertFalse(result["publication_authorized"])

    def test_empty_or_partial_evidence_does_not_prove_absence(self):
        data = parse_srt(
            cue(1, "00:00:01,000", "00:00:01,500") + "\n"
            + cue(2, "00:00:21,000", "00:00:22,000")
        )
        none = build_review_interval_manifest(data)
        self.assertEqual(none["intervals"][0]["provider_evidence_state"],
                         "PROVIDER_WORD_EVIDENCE_NOT_SUPPLIED")
        self.assertIsNone(none["intervals"][0]["provider_word_anchor_count"])
        partial = build_review_interval_manifest(
            data, provider_evidence=provider("b" * 64, False, []),
            expected_source_sha="b" * 64,
        )
        self.assertEqual(partial["intervals"][0]["provider_evidence_state"],
                         "UNKNOWN_PARTIAL_PROVIDER_INVENTORY")

    def test_wrong_source_and_word_bounds_fail_closed(self):
        data = parse_srt(cue(1, "00:00:01,000", "00:00:02,000"))
        with self.assertRaises(ValueError):
            build_review_interval_manifest(
                data, provider_evidence=provider("c" * 64, True, []),
                expected_source_sha="d" * 64,
            )
        with self.assertRaises(ValueError):
            build_review_interval_manifest(
                data, provider_evidence=provider(
                    "c" * 64, True, [{"start_ms": -100, "end_ms": 1}]),
                expected_source_sha="c" * 64,
            )
        with self.assertRaises(ValueError):
            build_review_interval_manifest(data, audio_duration_ms=1)

    def test_new_cli_output_and_collision_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            srt = root / "original.srt"
            srt.write_text(
                cue(1, "00:00:00,000", "00:00:00,500") + "\n"
                + cue(2, "00:00:20,000", "00:00:20,500"), encoding="utf-8",
            )
            out = root / "intervals.json"
            with patch("sys.argv", ["qa", "--srt", str(srt),
                                    "--review-intervals-output", str(out)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["interval_count"], 1)
            self.assertFalse(result["golden_qa_pass"])
            clash = root / "collision.json"
            with patch("sys.argv", ["qa", "--srt", str(srt),
                                    "--report-output", str(clash),
                                    "--review-intervals-output", str(clash)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            self.assertFalse(clash.exists())


if __name__ == "__main__":
    unittest.main()
