from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.canonical.golden_corpus import (
    GoldenCorpusIndex,
    error_memory_reference,
    golden_corpus_reference,
    records_from_reviewed_srt,
    write_jsonl,
)
from app.canonical.omission_detection import detect_bidirectional_omissions


SRT = """1
00:00:01,000 --> 00:00:03,000
這裡講到阿迦膩吒天。

2
00:00:03,100 --> 00:00:05,000
前五識轉為成所作智。

3
00:00:05,100 --> 00:00:07,000
俱生我執要慢慢斷除。
"""


class GoldenCorpusTests(unittest.TestCase):
    def test_reviewed_srt_becomes_retrievable_human_gold(self) -> None:
        records = records_from_reviewed_srt(
            SRT,
            lesson_id="16",
            lesson_date="20260705",
            source_name="16-review.srt",
        )
        index = GoldenCorpusIndex(records)
        hits = index.retrieve("ASR 聽成阿加尼吒天，前五識變成什麼智？", top_k=3)
        self.assertTrue(hits)
        self.assertTrue(any("阿迦膩吒天" in str(hit["text"]) for hit in hits))

    def test_reference_is_bounded_and_fail_closed_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            self.assertEqual(
                golden_corpus_reference([{"raw_text": "阿加尼吒天"}], data_dir),
                "",
            )
            path = data_dir / "canonical" / "golden-corpus.jsonl"
            write_jsonl(
                records_from_reviewed_srt(
                    SRT,
                    lesson_id="16",
                    lesson_date="20260705",
                    source_name="16-review.srt",
                ),
                path,
            )
            reference = golden_corpus_reference(
                [{"raw_text": "這裡講阿加尼吒天"}],
                data_dir,
                top_k=2,
            )
            self.assertIn("human-reviewed", reference)
            self.assertIn("阿迦膩吒天", reference)
            self.assertLessEqual(len(reference), 1800)

    def test_error_memory_only_surfaces_variants_present_in_window(self) -> None:
        reference = error_memory_reference(
            [{"raw_text": "東方阿楚佛，上至阿加尼吒天。"}]
        )
        self.assertIn("阿楚佛 → 阿閦佛", reference)
        self.assertIn("阿加尼吒天 → 阿迦膩吒天", reference)
        self.assertNotIn("舍利佛 → 舍利弗", reference)


class BidirectionalOmissionTests(unittest.TestCase):
    @staticmethod
    def _segments(texts: list[str]) -> list[dict[str, object]]:
        out = []
        for index, text in enumerate(texts):
            out.append(
                {
                    "segment_id": f"s{index}",
                    "start_ms": index * 10_000,
                    "end_ms": index * 10_000 + 9_000,
                    "text": text,
                }
            )
        return out

    def test_reports_human_missing_without_modifying_source(self) -> None:
        source = self._segments(
            [
                "共同前文錨點內容很清楚。",
                "這一大段是真實講師補充內容而人工逐字稿完全省略而且足夠長足夠明確。",
                "共同後文錨點內容也很清楚。",
            ]
        )
        report = detect_bidirectional_omissions(
            source,
            "共同前文錨點內容很清楚。共同後文錨點內容也很清楚。",
            min_gap_chars=20,
            min_source_gap_ms=5_000,
        )
        self.assertTrue(any(c["direction"] == "human_missing" for c in report["candidates"]))
        self.assertFalse(report["source_modified"])

    def test_reports_source_missing_when_human_has_extra_speech(self) -> None:
        source = self._segments(
            [
                "共同前文錨點內容很清楚。",
                "共同後文錨點內容也很清楚。",
            ]
        )
        human = (
            "共同前文錨點內容很清楚。"
            "這一大段人工金標內容在來源ASR中完全漏掉而且足夠長足夠明確。"
            "共同後文錨點內容也很清楚。"
        )
        report = detect_bidirectional_omissions(
            source,
            human,
            min_gap_chars=20,
        )
        self.assertTrue(any(c["direction"] == "source_missing" for c in report["candidates"]))
        self.assertFalse(report["human_modified"])


if __name__ == "__main__":
    unittest.main()
