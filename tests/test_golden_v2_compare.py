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
            self.assertEqual(c["source_time_unit"], "milliseconds")
            self.assertTrue(c["chirp_evidence_ids"] or c["kind"] == "GEMINI_UNSUPPORTED_ADDITION")

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

    def test_large_unanchored_comparison_is_explicit_review_with_no_time(self) -> None:
        result = compare_gemini_chirp("很"*14000, WORDS)
        self.assertEqual(result["unaligned_long_spans"], 1)
        item = result["candidates"][0]
        self.assertIsNone(item["source_time_start"])
        self.assertEqual(item["chirp_evidence_ids"], [])

    def test_long_unique_anchor_alignment_preserves_real_chirp_time(self) -> None:
        words = [
            {"word": f"段{i:05d}甲乙", "start_ms": 100*i, "end_ms": 100*i + 90}
            for i in range(2200)
        ]
        text = "".join(w["word"] for w in words)
        changed = text.replace("段01000甲乙", "段01000誤字")
        result = compare_gemini_chirp(changed, words, max_characters=3000,
                                     source_sha256="b" * 64)
        self.assertFalse(result["exact_normalized_match"])
        self.assertEqual(result["unaligned_long_spans"], 0)
        self.assertTrue(any(c["source_time_start"] is not None for c in result["candidates"]))
        self.assertTrue(all(c["gemini_timing_evidence"] is None for c in result["candidates"]))

    def test_repeated_unanchored_long_speech_does_not_fabricate_time(self) -> None:
        words = [{"word": "阿彌陀佛", "start_ms": i*200,
                  "end_ms": i*200+190} for i in range(3000)]
        altered = "阿彌陀佛"*1500 + "另有一句" + "阿彌陀佛"*1500
        result = compare_gemini_chirp(altered, words, max_characters=2000)
        self.assertTrue(result["unaligned_long_spans"])
        self.assertTrue(all(c["source_time_start"] is None for c in result["candidates"]))


if __name__ == "__main__":
    unittest.main()
