"""Deterministic no-network tests. Never include private lecture transcripts here."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.golden_v2_offline_srt_timeline_qa import (
    inspect, merge_short_cues, parse_srt, render_srt, main,
)


def cue(n, start, end, text):
    return f"{n}\n{start} --> {end}\n{text}\n"


class GoldenV2OfflineTimelineQATests(unittest.TestCase):
    def test_long_text_with_huge_gap_is_flagged_not_interpolated(self):
        original = (
            cue(1, "00:00:00,000", "00:00:03,600", "測" * 656)
            + "\n"
            + cue(2, "00:06:53,800", "00:06:55,000", "接續")
        )
        report, draft = inspect(original, reference="測" * 656 + "接續",
                                draft_merge_short=True)
        self.assertTrue(report["reference_text_exact_ignoring_whitespace"])
        self.assertEqual(report["overlong_text_cues"][0]["characters"], 656)
        self.assertEqual(report["large_gaps"][0]["duration_ms"], 410200)
        self.assertEqual(report["safe_adjacent_merges"], 0)
        self.assertFalse(report["candidate"]["timestamp_interpolation"])
        self.assertEqual(parse_srt(draft), parse_srt(original))
        self.assertFalse(report["golden_qa_pass"])

    def test_conservative_merge_preserves_all_words_and_timestamp_extents(self):
        original = (
            cue(1, "00:00:00,000", "00:00:00,160", "我")
            + "\n"
            + cue(2, "00:00:00,200", "00:00:01,200", "們修行。")
            + "\n"
            + cue(3, "00:00:03,000", "00:00:04,000", "後續。")
        )
        report, draft = inspect(original, reference="我 們修行。 後續。",
                                draft_merge_short=True)
        after = parse_srt(draft)
        self.assertEqual(report["safe_adjacent_merges"], 1)
        self.assertEqual(len(after), 2)
        self.assertEqual((after[0].start_ms, after[0].end_ms), (0, 1200))
        self.assertEqual(after[0].text, "我們修行。")
        self.assertEqual(after[1].start_ms, 3000)
        self.assertEqual(report["candidate"]["short_cue_count"], 0)
        self.assertTrue(report["candidate"]["reference_text_exact_ignoring_whitespace"])
        self.assertFalse(report["candidate"]["publication_authorized"])

    def test_no_merge_over_large_gap_or_when_text_overflows(self):
        source = (
            cue(1, "00:00:00,000", "00:00:00,100", "甲")
            + "\n"
            + cue(2, "00:00:00,800", "00:00:01,200", "乙")
            + "\n"
            + cue(3, "00:00:01,200", "00:00:01,400", "中" * 43)
        )
        cues = parse_srt(source)
        after, merges = merge_short_cues(cues)
        self.assertEqual(merges, 0)
        self.assertEqual(len(after), 3)

    def test_text_comparison_is_whitespace_normalized_not_semantic(self):
        data = cue(1, "00:00:00,000", "00:00:01,000", "佛 性")
        yes, _ = inspect(data, reference="佛\n性")
        no, _ = inspect(data, reference="佛心")
        self.assertTrue(yes["reference_text_exact_ignoring_whitespace"])
        self.assertFalse(no["reference_text_exact_ignoring_whitespace"])
        self.assertEqual(yes["status"], "NEEDS_AUDIO_REVIEW")

    def test_invalid_cue_sequence_and_overlap_fail_closed(self):
        out_of_order = (
            cue(1, "00:00:00,000", "00:00:01,000", "一")
            + "\n"
            + cue(3, "00:00:01,000", "00:00:02,000", "二")
        )
        with self.assertRaises(ValueError):
            parse_srt(out_of_order)
        overlap = (
            cue(1, "00:00:00,000", "00:00:01,000", "一")
            + "\n"
            + cue(2, "00:00:00,900", "00:00:02,000", "二")
        )
        with self.assertRaises(ValueError):
            parse_srt(overlap)
        bad_clock = cue(1, "00:00:60,000", "00:00:61,000", "一")
        with self.assertRaises(ValueError):
            parse_srt(bad_clock)

    def test_cli_does_not_overwrite_source_and_never_returns_release_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.srt"
            source.write_text(cue(1, "00:00:00,000", "00:00:00,150", "甲"), encoding="utf-8")
            report_path = root / "report.json"
            preview = root / "review_only.srt"
            args = ["qa", "--srt", str(source), "--report-output", str(report_path),
                    "--draft-output", str(preview)]
            buf = io.StringIO()
            with patch("sys.argv", args), contextlib.redirect_stdout(buf):
                code = main()
            self.assertEqual(code, 2)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["golden_qa_pass"])
            self.assertFalse(report["publication_authorized"])
            self.assertTrue(preview.exists())
            self.assertIn("甲", preview.read_text(encoding="utf-8"))
            with patch("sys.argv", ["qa", "--srt", str(source),
                                   "--draft-output", str(source)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            self.assertEqual(source.read_text(encoding="utf-8"),
                             cue(1, "00:00:00,000", "00:00:00,150", "甲"))


if __name__ == "__main__":
    unittest.main()
