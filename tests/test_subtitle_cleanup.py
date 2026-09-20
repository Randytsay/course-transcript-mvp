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

    def test_triple_cleanup_preserves_legitimate_reduplication(self) -> None:
        cases = {
            "慢慢慢慢的增上,": "慢慢的增上,",
            "通通通通滿我們的願,": "通通滿我們的願,",
            "剛剛剛剛這些都是拖我們下六道輪迴,": "剛剛這些都是拖我們下六道輪迴,",
            "我們常常常常講,": "我們常常講,",
            "我們天天天天在睡覺,": "我們天天在睡覺,",
            "種種種種的現象,": "種種的現象,",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                cleaned, actions = clean_text(source)
                self.assertEqual(cleaned, expected)
                self.assertIn("triple_stutter", actions)

    def test_triple_cleanup_preserves_left_word_plus_reduplication(self) -> None:
        source = "六種神通通通成就。"
        cleaned, actions = clean_text(source)
        self.assertEqual(cleaned, source)
        self.assertNotIn("triple_stutter", actions)

    def test_triple_cleanup_keeps_human_approved_single_char_collapse(self) -> None:
        cleaned, actions = clean_text("我們的新新新年的新課程,")
        self.assertEqual(cleaned, "我們的新年的新課程,")
        self.assertIn("triple_stutter", actions)

    def test_multi_character_repetition_is_review_only(self) -> None:
        source = "這個這個這個因緣,有沒有有沒有有沒有自性?"
        cleaned, actions = clean_text(source)
        self.assertEqual(cleaned, source)
        self.assertNotIn("triple_stutter", actions)
        report = build_report(
            "subtitles-corrected.json",
            [{"segment_id": "seg-1", "start_ms": 0, "end_ms": 3000, "raw_text": source, "corrected_text": source}],
        )
        reasons = report["review_required"][0]["reasons"]
        self.assertIn("possible_repeated_phrase", reasons)

    def test_collapses_high_confidence_single_character_stutters(self) -> None:
        cases = {
            "就是這這種布施,": "就是這種布施,",
            "我我會我會三寶法員嘛,": "我會我會三寶法員嘛,",
            "要要念觀世音菩薩嘛,": "要念觀世音菩薩嘛,",
            "不一定會知,他他離苦,": "不一定會知,他離苦,",
            "沒有一些資資糧很痛苦,": "沒有一些資糧很痛苦,",
            "就是慢慢的慈慈悲心擴大。": "就是慢慢的慈悲心擴大。",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                cleaned, actions = clean_text(source)
                self.assertEqual(cleaned, expected)
                self.assertIn("double_stutter_high_confidence", actions)

    def test_protects_lexical_boundaries_and_legitimate_reduplication(self) -> None:
        cases = (
            "我現在在修一日一夜,",
            "可以以此發願,以此觀想,",
            "有沒有有沒有自性?",
            "還有有時候五位,",
            "不是自己成就就成就就好,",
            "因為為什麼要回向?",
            "作為為護法的規範,",
            "不是世間人所得得見的,",
            "般若若能照見五蘊皆空,",
            "那慢慢慈悲親人之後,",
            "如來通通有教授,",
            "自己迷迷糊糊,",
            "弟子某某,",
            "這些我們會產生一點點的驕慢,",
            "自己就災難連連啊,",
        )
        for source in cases:
            with self.subTest(source=source):
                cleaned, actions = clean_text(source)
                self.assertEqual(cleaned, source)
                self.assertNotIn("double_stutter_high_confidence", actions)

    def test_does_not_destutter_mantra_phonetics(self) -> None:
        source = "南無那丹那丹那耶耶,阿囉囉帝三藐三佛陀耶,"
        cleaned, actions = clean_text(source)
        self.assertEqual(cleaned, source)
        self.assertNotIn("double_stutter_high_confidence", actions)

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

    def test_noisy_closing_two_cycle_mantra_uses_canonical_display_only(self) -> None:
        noisy_tail = (
            "前言",
            "南無納丹納丹納耶", "阿啦囉狄三藐三菩提耶", "摩訶菩提", "梭呵",
            "南無納丹納丹納耶", "阿啦囉狄三佛陀耶", "菩提摩訶", "索呵",
            "南無毗盧佛", "三藐三佛", "菩提", "梭呵", "大眾請起立", "問訊",
            "後續禮儀", "後續禮儀", "後續禮儀", "後續禮儀", "後續禮儀",
            "後續禮儀", "後續禮儀", "後續禮儀", "後續禮儀", "後續禮儀",
        )
        segments = [
            {
                "segment_id": f"seg-{index:03d}",
                "start_ms": index * 1_000,
                "end_ms": (index + 1) * 1_000,
                "raw_text": text,
                "corrected_text": text,
            }
            for index, text in enumerate(noisy_tail)
        ]
        report = build_report("subtitles.json", segments, content_mode="dacheng_buddhist")
        self.assertTrue(report["mantra"]["applied"])
        self.assertEqual(report["mantra"]["match"], "fuzzy_closing_two_cycle")
        self.assertEqual(len(report["segments"]), len(segments))
        display = report["display_segments"]
        self.assertTrue(any(item["segment_id"] == "mantra-display-001" for item in display))
        canonical = next(item for item in display if item["segment_id"] == "mantra-display-001")
        self.assertTrue(canonical["cleaned_text"].startswith("《得見彌勒根本大明神咒》"))
        self.assertTrue(any(item["cleaned_text"] == "大眾請起立" for item in display))


if __name__ == "__main__":
    unittest.main()
