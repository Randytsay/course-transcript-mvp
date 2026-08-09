from __future__ import annotations

import unittest

from app.providers.subtitle_cleanup import build_report, clean_text


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


if __name__ == "__main__":
    unittest.main()
