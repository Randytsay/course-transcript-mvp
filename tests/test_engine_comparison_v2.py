"""Non-paid comparisons; never accept LLM insertions without audio."""
import unittest

from app.providers.engine_comparison_v2 import compare_primary_to_chirp


class ThreeWayEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.words = [
            {"word": "復次", "start_ms": 100, "end_ms": 400},
            {"word": "文殊師利", "start_ms": 400, "end_ms": 1200},
            {"word": "男身具七寶", "start_ms": 1300, "end_ms": 2600},
        ]

    def test_matching_transcripts_have_no_issues(self):
        result = compare_primary_to_chirp(
            primary_text="復次文殊師利，男身具七寶。",
            chirp_words=self.words,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["issues"], [])
        self.assertFalse(result["auto_apply"])

    def test_scripture_wording_is_flagged_and_timed_from_chirp(self):
        result = compare_primary_to_chirp(
            primary_text="復次文殊師利，男生具有七寶。",
            chirp_words=self.words,
            transcribe_text="復次文殊師利。男身具七寶。",
        )
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertIn("WORDING_CONFLICT", {x["type"] for x in result["issues"]})
        self.assertTrue(all(x["source_start_ms"] >= 100 for x in result["issues"]))
        self.assertTrue(all(x["adjudication"] == "NEEDS_AUDIO_REVIEW" for x in result["issues"]))
        self.assertFalse(result["auto_apply"])

    def test_hallucinated_end_is_not_auto_written(self):
        result = compare_primary_to_chirp(
            primary_text="復次文殊師利男身具七寶沒有唸出的句子",
            chirp_words=self.words,
        )
        inserted = [x for x in result["issues"] if x["type"] == "GEMINI_POSSIBLE_ADDITION"]
        self.assertTrue(inserted)
        self.assertEqual(inserted[-1]["timestamp_kind"], "chirp_context_window")
        self.assertFalse(result["auto_apply"])

    def test_chirp_only_content_can_indicate_gemini_omission(self):
        result = compare_primary_to_chirp(
            primary_text="復次文殊師利",
            chirp_words=self.words,
        )
        self.assertIn("GEMINI_POSSIBLE_OMISSION", {x["type"] for x in result["issues"]})

    def test_invalid_chirp_timeline_is_rejected(self):
        with self.assertRaises(ValueError):
            compare_primary_to_chirp(primary_text="好", chirp_words=[
                {"word": "a", "start_ms": 2000, "end_ms": 3000},
                {"word": "b", "start_ms": 2000, "end_ms": 2100},
            ])

    def test_empty_recognition_requires_review(self):
        result = compare_primary_to_chirp(primary_text="", chirp_words=self.words)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertFalse(result["auto_apply"])


if __name__ == "__main__":
    unittest.main()
