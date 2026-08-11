from __future__ import annotations

import unittest

from app.providers.subtitle_cleanup import build_report, clean_text
from app.providers.mantra_context import MANTRA_LINES


class SubtitleCleanupTests(unittest.TestCase):
    def test_removes_only_high_confidence_boundary_noise(self) -> None:
        cleaned, actions = clean_text("嗯嗯嗯我我我今天來了喔")
        self.assertEqual(cleaned, "我今天來了")
        self.assertIn("boundary_filler_prefix", actions)
        self.assertIn("triple_stutter", actions)

    def test_keeps_inner_filler_for_review(self) -> None:
        cleaned, actions = clean_text("這是啊一段內容")
        self.assertEqual(cleaned, "這是啊一段內容")
        self.assertEqual(actions, [])
        report = build_report(
            "subtitles-corrected.json",
            [{"segment_id": "seg-1", "start_ms": 0, "end_ms": 2000, "raw_text": cleaned, "corrected_text": cleaned}],
        )
        self.assertIn("inner_filler_review", report["review_required"][0]["reasons"])

    def test_preserves_timing_and_flags_duplicate_cues(self) -> None:
        report = build_report(
            "subtitles.json",
            [
                {"segment_id": "seg-1", "start_ms": 0, "end_ms": 1000, "raw_text": "甲", "corrected_text": "甲"},
                {"segment_id": "seg-2", "start_ms": 1000, "end_ms": 2000, "raw_text": "甲", "corrected_text": "甲"},
            ],
        )
        self.assertEqual([(item["segment_id"], item["start_ms"], item["end_ms"]) for item in report["segments"]], [("seg-1", 0, 1000), ("seg-2", 1000, 2000)])
        self.assertEqual(report["summary"]["possible_duplicate_cue_count"], 1)
        self.assertEqual(report["status"], "REVIEW")

    def test_two_complete_mantra_cycles_use_display_layer_only(self) -> None:
        segments = [
            {
                "segment_id": f"seg-{index:03d}",
                "start_ms": index * 1_000,
                "end_ms": (index + 1) * 1_000,
                "raw_text": line,
                "corrected_text": line,
            }
            for index, line in enumerate(("課程結尾", *MANTRA_LINES, *MANTRA_LINES))
        ]
        report = build_report("subtitles.json", segments, content_mode="dacheng_buddhist")
        self.assertTrue(report["mantra"]["applied"])
        self.assertEqual(len(report["segments"]), len(segments))
        self.assertEqual(len(report["display_segments"]), len(segments) - len(MANTRA_LINES))
        self.assertEqual(report["segments"][1]["cleaned_text"], MANTRA_LINES[0])
        self.assertTrue(report["display_segments"][1]["cleaned_text"].startswith("《得見彌勒根本大明神咒》"))
        self.assertFalse(any(not item["cleaned_text"] for item in report["display_segments"]))

    def test_scattered_mantra_anchors_never_suppress_speech(self) -> None:
        segments = [
            {
                "segment_id": f"seg-{index:03d}",
                "start_ms": index * 1_000,
                "end_ms": (index + 1) * 1_000,
                "raw_text": text,
                "corrected_text": text,
            }
            for index, text in enumerate(("前言", MANTRA_LINES[0], "正常講課內容", MANTRA_LINES[1], "結語"))
        ]
        report = build_report("subtitles.json", segments, content_mode="dacheng_buddhist")
        self.assertFalse(report["mantra"]["applied"])
        self.assertEqual(
            [item["cleaned_text"] for item in report["display_segments"]],
            [item["corrected_text"] for item in segments],
        )


if __name__ == "__main__":
    unittest.main()
