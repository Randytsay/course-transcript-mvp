from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
