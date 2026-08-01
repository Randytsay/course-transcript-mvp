from __future__ import annotations

import unittest
from unittest.mock import patch

from app.providers import build_srt
from app.providers.build_srt import segment_words


def char_words(text: str, milliseconds: int = 180) -> list[dict]:
    return [
        {"word": char, "start_ms": index * milliseconds, "end_ms": (index + 1) * milliseconds}
        for index, char in enumerate(text)
    ]


class SubtitleSegmentationTests(unittest.TestCase):
    def test_never_splits_the_chinese_word_shijian(self) -> None:
        source = "有說，我在日本讀書，在日本花了8年的時間去拿到我營養學博士的學位，"
        segments = segment_words(char_words(source))
        text = "".join(segment["raw_text"] for segment in segments)
        self.assertEqual(text, source)
        self.assertTrue(any("時間" in segment["raw_text"] for segment in segments))
        self.assertFalse(any(segment["raw_text"].endswith("時") for segment in segments))
        self.assertFalse(any(segment["raw_text"].startswith("間") for segment in segments))

    def test_respects_real_speech_gaps(self) -> None:
        words = char_words("第一句第二句", 200)
        words[3]["start_ms"] += 2_000
        words[3]["end_ms"] += 2_000
        words[4]["start_ms"] += 2_000
        words[4]["end_ms"] += 2_000
        words[5]["start_ms"] += 2_000
        words[5]["end_ms"] += 2_000
        segments = segment_words(words)
        self.assertGreaterEqual(len(segments), 2)

    def test_never_cuts_between_lexical_pieces_of_one_asr_word(self) -> None:
        """A multi-character ASR word may be split by jieba but has one timing."""
        words = [
            {"word": "甲", "start_ms": 0, "end_ms": 1_000},
            {"word": "專有名詞", "start_ms": 1_000, "end_ms": 2_000},
            {"word": "乙", "start_ms": 4_000, "end_ms": 4_500},
        ]
        with (
            patch.object(
                build_srt.jieba,
                "lcut",
                return_value=["甲", "專有", "名詞", "乙"],
            ),
            patch.object(build_srt, "MAX_CHARS", 2),
            patch.object(build_srt, "TARGET_MIN_MS", 0),
        ):
            segments = segment_words(words)

        self.assertEqual("".join(segment["raw_text"] for segment in segments), "甲專有名詞乙")
        self.assertTrue(any("專有名詞" in segment["raw_text"] for segment in segments))
        self.assertTrue(all(segment["end_ms"] > segment["start_ms"] for segment in segments))

    def test_merges_adjacent_cue_when_chirp_timing_collides(self) -> None:
        """A shared Chirp boundary must not create an invalid zero-length cue."""
        words = [
            {"word": "甲。", "start_ms": 0, "end_ms": 1_000},
            {"word": "乙", "start_ms": 500, "end_ms": 1_000},
        ]
        with (
            patch.object(build_srt.jieba, "lcut", return_value=["甲", "。", "乙"]),
            patch.object(build_srt, "TARGET_MIN_MS", 0),
        ):
            segments = segment_words(words)

        self.assertEqual("".join(segment["raw_text"] for segment in segments), "甲。乙")
        self.assertEqual(len(segments), 1)
        self.assertEqual((segments[0]["start_ms"], segments[0]["end_ms"]), (0, 1_000))
        self.assertTrue(segments[0]["timing_collision_merged"])
        self.assertEqual(segments[0]["timing_collision_word_ranges"][0]["word_start"], 1)


if __name__ == "__main__":
    unittest.main()
