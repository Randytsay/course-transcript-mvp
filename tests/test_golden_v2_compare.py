"""Offline differential comparator never invents missing timestamps."""
from __future__ import annotations

import unittest

from app.providers.golden_v2_compare import AlignmentNeedsReview, compare_gemini_chirp


WORDS = [
    {"word": "復次", "start_ms": 100, "end_ms": 400},
    {"word": "文殊師利", "start_ms": 400, "end_ms": 1100},
    {"word": "男身具七寶", "start_ms": 1200, "end_ms": 2400},
]


class ThreeEnginePreparationTests(unittest.TestCase):
    def test_equal_text_is_match_not_accuracy_claim(self) -> None:
        result = compare_gemini_chirp("復次文殊師利，男身具七寶。", WORDS)
        self.assertTrue(result["exact_normalized_match"])
        self.assertEqual(result["pending_review_count"], 0)
        self.assertEqual(result["acoustic_coverage"], "NOT_DETERMINED_BY_TEXT_DIFF")

    def test_disagreement_is_review_only_with_real_chirp_timing(self) -> None:
        result = compare_gemini_chirp("復次文殊師利，男生具有七寶。", WORDS)
        self.assertGreater(result["pending_review_count"], 0)
        for c in result["candidates"]:
            self.assertEqual(c["comparison_status"], "REVIEW_REQUIRED")
            self.assertIsNone(c["gemini_timing_evidence"])
            self.assertFalse(c["auto_replacement_allowed"])

    def test_gemini_inserted_text_is_never_given_fabricated_timing(self) -> None:
        result = compare_gemini_chirp("復次文殊師利另外加上一句男身具七寶", WORDS)
        insertions = [c for c in result["candidates"] if c["kind"] == "GEMINI_UNSUPPORTED_ADDITION"]
        self.assertTrue(insertions)
        self.assertTrue(all(c["chirp_timing_evidence"] is None for c in insertions))

    def test_chirp_content_missing_from_gemini_is_flagged(self) -> None:
        result = compare_gemini_chirp("復次文殊師利", WORDS)
        self.assertIn("GEMINI_POSSIBLE_OMISSION", {c["kind"] for c in result["candidates"]})

    def test_timestamps_must_be_monotonic_and_trustworthy(self) -> None:
        broken = [dict(WORDS[0]), {"word": "錯", "start_ms": 200, "end_ms": 500}]
        with self.assertRaises(AlignmentNeedsReview):
            compare_gemini_chirp("復次錯", broken)

    def test_large_unanchored_comparison_fails_closed(self) -> None:
        with self.assertRaises(AlignmentNeedsReview):
            compare_gemini_chirp("很"*14000, WORDS)


if __name__ == "__main__":
    unittest.main()
