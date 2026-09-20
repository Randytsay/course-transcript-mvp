from __future__ import annotations

import unittest

from app.providers.boundary_integrity import scan_boundary_integrity


class BoundaryIntegrityTests(unittest.TestCase):
    def test_detects_repeated_lesson_term_split_across_close_boundary(self) -> None:
        segments = [
            {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "cleaned_text": "願一切眾生得安樂。"},
            {"segment_id": "s2", "start_ms": 1100, "end_ms": 2000, "cleaned_text": "眾生都有佛性。"},
            {"segment_id": "s3", "start_ms": 2100, "end_ms": 3000, "cleaned_text": "度化眾生。"},
            {"segment_id": "s4", "start_ms": 3100, "end_ms": 4000, "cleaned_text": "利益眾生。"},
            {"segment_id": "s5", "start_ms": 4100, "end_ms": 5000, "cleaned_text": "慈悲眾生。"},
            {"segment_id": "s6", "start_ms": 5100, "end_ms": 6000, "cleaned_text": "攝受眾生。"},
            {"segment_id": "s7", "start_ms": 6100, "end_ms": 7000, "cleaned_text": "所有眾生。"},
            {"segment_id": "s8", "start_ms": 7100, "end_ms": 8000, "cleaned_text": "一切眾生。"},
            {"segment_id": "s9", "start_ms": 8100, "end_ms": 9000, "cleaned_text": "這些眾生。"},
            {"segment_id": "s10", "start_ms": 9100, "end_ms": 10000, "cleaned_text": "十類眾生。"},
            {"segment_id": "a", "start_ms": 10100, "end_ms": 11000, "cleaned_text": "攝受十種眾"},
            {"segment_id": "b", "start_ms": 11000, "end_ms": 12000, "cleaned_text": "生,十類眾生,"},
        ]
        report = scan_boundary_integrity(segments)
        candidates = report["split_candidates"]
        self.assertTrue(
            any(
                item["term"] == "眾生"
                and item["before_segment_id"] == "a"
                and item["after_segment_id"] == "b"
                for item in candidates
            )
        )

    def test_allows_frequent_two_character_term_starting_with_glue_character(self) -> None:
        intact = [
            {
                "segment_id": f"i{index}",
                "start_ms": index * 1000,
                "end_ms": index * 1000 + 900,
                "cleaned_text": "發願往生淨土。",
            }
            for index in range(12)
        ]
        segments = [
            *intact,
            {"segment_id": "a", "start_ms": 13000, "end_ms": 14000, "cleaned_text": "一輩子到往"},
            {"segment_id": "b", "start_ms": 14000, "end_ms": 15000, "cleaned_text": "生都還不能解脫。"},
        ]
        report = scan_boundary_integrity(segments)
        self.assertTrue(
            any(
                item["term"] == "往生"
                and item["before_segment_id"] == "a"
                and item["after_segment_id"] == "b"
                for item in report["split_candidates"]
            )
        )

    def test_does_not_promote_grammatical_phrase_to_lexical_split(self) -> None:
        segments = [
            {"segment_id": "i1", "start_ms": 0, "end_ms": 900, "cleaned_text": "佛的智慧非常深。"},
            {"segment_id": "i2", "start_ms": 1000, "end_ms": 1900, "cleaned_text": "佛的智慧不可思議。"},
            {"segment_id": "a", "start_ms": 2000, "end_ms": 2900, "cleaned_text": "我們要將這些佛的"},
            {"segment_id": "b", "start_ms": 2900, "end_ms": 3900, "cleaned_text": "智慧轉化到生活。"},
        ]
        report = scan_boundary_integrity(segments)
        self.assertFalse(any(item["term"] == "佛的智慧" for item in report["split_candidates"]))

    def test_punctuated_boundary_is_not_treated_as_lexical_split(self) -> None:
        segments = [
            {"segment_id": "a", "start_ms": 0, "end_ms": 1000, "cleaned_text": "講到眾,"},
            {"segment_id": "b", "start_ms": 1040, "end_ms": 2000, "cleaned_text": "生起慈悲心。"},
        ]
        report = scan_boundary_integrity(segments)
        self.assertEqual(report["split_candidate_count"], 0)

    def test_reports_structural_punctuation_anomalies(self) -> None:
        segments = [
            {"segment_id": "a", "start_ms": 0, "end_ms": 1000, "cleaned_text": ",不應段首標點"},
            {"segment_id": "b", "start_ms": 1040, "end_ms": 2000, "cleaned_text": "句子,,有重複標點"},
        ]
        report = scan_boundary_integrity(segments)
        self.assertEqual(report["leading_punctuation_count"], 1)
        self.assertEqual(report["repeated_punctuation_count"], 1)


if __name__ == "__main__":
    unittest.main()
