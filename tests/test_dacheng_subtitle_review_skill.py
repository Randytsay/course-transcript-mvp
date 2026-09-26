from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.canonical.defaults import MANTRA_BODY, MANTRA_TITLE, SCRIPTURE_KEY
from app.canonical.store import CanonicalTextStore
from app.skills.dacheng_subtitle_review import (
    CONTENT_MODE,
    align_human_gold,
    blind_audit,
    review_lesson,
    semantic_segmentation,
    sequential_word_timed_human_display_layer,
    word_timed_human_display_layer,
    word_timed_mantra_region_layer,
    word_timed_scripture_verification_layer,
    write_review_bundle,
)


def _srt(cues: list[tuple[int, int, str]]) -> str:
    def stamp(value: int) -> str:
        h, value = divmod(value, 3_600_000)
        m, value = divmod(value, 60_000)
        s, ms = divmod(value, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    return (
        "\n\n".join(
            f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}"
            for index, (start, end, text) in enumerate(cues, 1)
        )
        + "\n"
    )


class DachengSubtitleReviewSkillTests(unittest.TestCase):
    def _data_dir(self, temp: str) -> Path:
        data_dir = Path(temp)
        store = CanonicalTextStore(data_dir / "course-transcript.db")
        scripture_lines = [
            "我今攝受是諸人等，",
            "或以讀誦分別決定修多羅、毘尼、阿毘曇，",
            "為他演說、讚歎義味，",
            "不生嫉妬教於他人，",
            "令得受持，",
            "修諸功德來生我所；",
            "或以衣食施人、持戒、智慧，",
            "修此功德來生我所；",
            "或以持戒、忍辱，",
            "修淨慈心，",
            "以此功德來生我所；",
            "說是語已，",
            "稱讚釋迦牟尼佛：",
            "善哉，善哉！",
            "能於五濁惡世，",
            "教化如是等百千萬億諸惡眾生，",
            "令修善本，",
            "來生我所。",
        ]
        store.put_version(
            document_key=SCRIPTURE_KEY,
            title="《佛說彌勒大成佛經》",
            body_text="\n".join(scripture_lines),
            actor="test",
            source={"cbeta_id": "T0456", "volume": "T14"},
        )
        return data_dir

    def test_human_alignment_changes_supported_portion_and_preserves_omission(self) -> None:
        source = [
            {
                "segment_id": "a",
                "start_ms": 0,
                "end_ms": 2000,
                "raw_text": "修多羅毗尼阿毗壇",
                "cleaned_text": "修多羅毗尼阿毗壇",
            },
            {
                "segment_id": "b",
                "start_ms": 2100,
                "end_ms": 4000,
                "raw_text": "人工稿沒有這一句",
                "cleaned_text": "人工稿沒有這一句",
            },
        ]
        aligned, report = align_human_gold(
            source,
            "這裡說修多羅毘尼阿毘曇接著繼續說法",
            minimum_coverage=0.30,
            minimum_anchor_chars=3,
        )
        self.assertTrue(report["applied"])
        self.assertIn("修多羅", aligned[0]["cleaned_text"])
        self.assertEqual(aligned[1]["cleaned_text"], "人工稿沒有這一句")
        self.assertEqual(source[0]["raw_text"], "修多羅毗尼阿毗壇")

    def test_short_cue_is_merged_without_touching_source_object(self) -> None:
        source = [
            {
                "segment_id": "a",
                "start_ms": 0,
                "end_ms": 1800,
                "raw_text": "前句",
                "cleaned_text": "前句",
                "source_segment_ids": ["a"],
            },
            {
                "segment_id": "b",
                "start_ms": 1800,
                "end_ms": 1900,
                "raw_text": "短",
                "cleaned_text": "短",
                "source_segment_ids": ["b"],
            },
        ]
        final, metadata = semantic_segmentation(source)
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["cleaned_text"], "前句短")
        self.assertEqual(source[0]["cleaned_text"], "前句")
        self.assertEqual(len(metadata["pathological_short_merges"]), 1)

    def test_blind_audit_is_read_only_and_precedes_current_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = self._data_dir(temp)
            corpus = data_dir / "canonical" / "golden-corpus.jsonl"
            corpus.parent.mkdir(parents=True, exist_ok=True)
            corpus.write_text(
                json.dumps(
                    {"text": "歷史已審核內容", "stage": "lesson"},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            before = corpus.read_bytes()
            audit = blind_audit(
                [
                    {
                        "segment_id": "a",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "raw_text": "歷史已審核內容",
                    }
                ],
                data_dir=data_dir,
            )
            self.assertEqual(corpus.read_bytes(), before)
            self.assertEqual(
                audit["learning_policy"],
                "blind_snapshot_before_current_lesson_alignment",
            )

    def test_review_bundle_never_learns_current_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = self._data_dir(temp)
            lines = [
                "我今攝受是諸人等，",
                "或以讀誦分別決定修多羅、毘尼、阿毘曇，",
                "為他演說、讚歎義味，",
                "不生嫉妬教於他人，",
                "令得受持，",
                "修諸功德來生我所；",
                "或以衣食施人、持戒、智慧，",
                "修此功德來生我所；",
                "或以持戒、忍辱，",
                "修淨慈心，",
                "以此功德來生我所；",
                "說是語已，",
                "稱讚釋迦牟尼佛：",
                "善哉，善哉！",
                "能於五濁惡世，",
                "教化如是等百千萬億諸惡眾生，",
                "令修善本，",
                "來生我所。",
            ]
            cues = [
                (index * 1800, index * 1800 + 1600, line)
                for index, line in enumerate(lines)
            ]
            result = review_lesson(
                srt_text=_srt(cues),
                human_text="".join(lines),
                data_dir=data_dir,
                lesson_id="20",
                lesson_date="20260823",
            )
            self.assertFalse(result["learning_applied"])
            self.assertEqual(result["content_mode"], CONTENT_MODE)
            self.assertTrue(result["qa"]["raw_immutability"]["unchanged"])
            out = Path(temp) / "out"
            paths = write_review_bundle(
                result,
                output_dir=out,
                stem="lesson20_20260823_review",
            )
            self.assertTrue(Path(paths["srt"]).is_file())
            self.assertTrue(Path(paths["zip"]).is_file())

    def test_mantra_preserves_two_whole_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = self._data_dir(temp)
            cues: list[tuple[int, int, str]] = []
            cursor = 0
            for text in ["今天接著說明慈心。", "大眾合掌。", MANTRA_TITLE]:
                cues.append((cursor, cursor + 1200, text))
                cursor += 1300
            for _ in range(2):
                for line in MANTRA_BODY.splitlines():
                    cues.append((cursor, cursor + 900, line))
                    cursor += 1000
                    cues.append((cursor, cursor + 900, line))
                    cursor += 1000
            cues.append((cursor, cursor + 1200, "大眾起立"))
            human = "\n".join(
                ["今天接著說明慈心。", "大眾合掌。", MANTRA_TITLE]
                + MANTRA_BODY.splitlines() * 2
                + ["大眾起立"]
            )
            result = review_lesson(
                srt_text=_srt(cues),
                human_text=human,
                data_dir=data_dir,
                lesson_id="fixture",
                lesson_date="20260101",
            )
            self.assertEqual(result["qa"]["mantra"]["cycle_count"], 2)
            self.assertEqual(
                result["qa"]["mantra"]["cue_count"],
                2 * len(MANTRA_BODY.splitlines()),
            )
            self.assertTrue(result["qa"]["mantra"]["whole_cycles_preserved"])
            self.assertEqual(result["qa"]["new_golden_candidates"], [])

    def test_word_timed_typo_uses_original_chirp_word_timing(self) -> None:
        source = [
            {
                "segment_id": "s1",
                "start_ms": 0,
                "end_ms": 1600,
                "raw_text": "為法忘俱",
                "cleaned_text": "為法忘俱",
            }
        ]
        merged_words = {
            "words": [
                {"word": "為", "start_ms": 100, "end_ms": 300},
                {"word": "法", "start_ms": 350, "end_ms": 550},
                {"word": "忘", "start_ms": 600, "end_ms": 800},
                {"word": "俱", "start_ms": 900, "end_ms": 1150},
            ]
        }
        original = json.loads(json.dumps(source, ensure_ascii=False))
        display, meta = word_timed_human_display_layer(
            source,
            "為法忘軀。",
            {"candidates": []},
            merged_words,
        )
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["timing_source"], "chirp_word_timestamps")
        self.assertEqual(display[0]["cleaned_text"], "為法忘軀。")
        self.assertEqual(display[0]["start_ms"], 100)
        self.assertEqual(display[0]["end_ms"], 1150)
        self.assertEqual(display[0]["source_word_start_index"], 0)
        self.assertEqual(display[0]["source_word_end_index"], 3)
        self.assertEqual(source, original)

    def test_word_timed_semantic_units_preserve_real_pause(self) -> None:
        source = [
            {
                "segment_id": "s1",
                "start_ms": 0,
                "end_ms": 3000,
                "raw_text": "修行真好",
                "cleaned_text": "修行真好",
            }
        ]
        merged_words = {
            "words": [
                {"word": "修", "start_ms": 0, "end_ms": 300},
                {"word": "行", "start_ms": 400, "end_ms": 700},
                {"word": "真", "start_ms": 2000, "end_ms": 2300},
                {"word": "好", "start_ms": 2400, "end_ms": 2700},
            ]
        }
        display, meta = word_timed_human_display_layer(
            source,
            "修行，真好。",
            {"candidates": []},
            merged_words,
        )
        self.assertEqual(meta["timing_source"], "chirp_word_timestamps")
        self.assertEqual(len(display), 2)
        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in display],
            [(0, 700), (2000, 2700)],
        )
        self.assertEqual(display[1]["start_ms"] - display[0]["end_ms"], 1300)
        word_edges = {0, 300, 400, 700, 2000, 2300, 2400, 2700}
        for item in display:
            self.assertIn(item["start_ms"], word_edges)
            self.assertIn(item["end_ms"], word_edges)

    def test_context_projection_snaps_to_existing_word_boundaries(self) -> None:
        source = [
            {
                "segment_id": "s1",
                "start_ms": 0,
                "end_ms": 2300,
                "raw_text": "甲乙錯錯丙丁",
                "cleaned_text": "甲乙錯錯丙丁",
            }
        ]
        merged_words = {
            "words": [
                {"word": "甲", "start_ms": 0, "end_ms": 200},
                {"word": "乙", "start_ms": 300, "end_ms": 500},
                {"word": "錯", "start_ms": 800, "end_ms": 1000},
                {"word": "錯", "start_ms": 1100, "end_ms": 1300},
                {"word": "丙", "start_ms": 1700, "end_ms": 1900},
                {"word": "丁", "start_ms": 2000, "end_ms": 2200},
            ]
        }
        display, meta = word_timed_human_display_layer(
            source,
            "甲乙，正確，丙丁。",
            {"candidates": []},
            merged_words,
        )
        projected = [
            item
            for item in display
            if item.get("human_mapping_method") == "context_projected_word_boundary"
        ]
        self.assertEqual(meta["context_projected_units"], 1)
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["cleaned_text"], "正確，")
        self.assertEqual(projected[0]["start_ms"], 800)
        self.assertEqual(projected[0]["end_ms"], 1300)

    def test_unmapped_run_projects_only_across_chirp_word_indexes(self) -> None:
        source_text = "甲乙" + ("錯" * 200) + "丙丁"
        source = [
            {
                "segment_id": "s1",
                "start_ms": 0,
                "end_ms": 50_000,
                "raw_text": source_text,
                "cleaned_text": source_text,
            }
        ]
        merged_words = {"words": []}
        for index, char in enumerate(source_text):
            merged_words["words"].append(
                {
                    "word": char,
                    "start_ms": index * 200,
                    "end_ms": index * 200 + 120,
                }
            )
        middle_units = [("真" * 20) + "，" for _ in range(10)]
        human = "甲乙，" + "".join(middle_units) + "丙丁。"
        display, meta = word_timed_human_display_layer(
            source,
            human,
            {"candidates": []},
            merged_words,
        )
        run_projected = [
            item
            for item in display
            if item.get("human_mapping_method") == "context_projected_word_run"
        ]
        self.assertEqual(meta["context_projected_run_units"], 10)
        self.assertEqual(
            [item["cleaned_text"] for item in run_projected],
            middle_units,
        )
        word_edges = {
            edge
            for index in range(len(source_text))
            for edge in (index * 200, index * 200 + 120)
        }
        for item in run_projected:
            self.assertIn(item["start_ms"], word_edges)
            self.assertIn(item["end_ms"], word_edges)

    def test_sequential_alignment_never_reuses_earlier_repeated_phrase(self) -> None:
        source = [
            {
                "segment_id": "s1",
                "start_ms": 0,
                "end_ms": 8000,
                "raw_text": "前段共同句前段結束中間雜訊共同句後段正確",
                "cleaned_text": "前段共同句前段結束中間雜訊共同句後段正確",
            }
        ]
        merged_words = {"words": []}
        source_text = "前段共同句前段結束中間雜訊共同句後段正確"
        for index, char in enumerate(source_text):
            merged_words["words"].append(
                {
                    "word": char,
                    "start_ms": index * 300,
                    "end_ms": index * 300 + 180,
                }
            )
        human = "前段共同句，前段結束。\n共同句，後段正確。"
        display, meta = sequential_word_timed_human_display_layer(
            source,
            human,
            {"candidates": []},
            merged_words,
        )
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["alignment_strategy"], "sequential_forward_paragraphs")
        later = [item for item in display if item["cleaned_text"] == "共同句，"][-1]
        later_occurrence_index = source_text.rfind("共同句")
        self.assertGreaterEqual(
            later["start_ms"],
            later_occurrence_index * 300,
        )
        first_paragraph_end = max(
            item["end_ms"]
            for item in display
            if item["cleaned_text"] in {"前段共同句，", "前段結束。"}
        )
        self.assertGreaterEqual(later["start_ms"], first_paragraph_end)

    def test_word_timed_scripture_verification_keeps_existing_timing(self) -> None:
        display = [
            {
                "segment_id": "s1",
                "start_ms": 100,
                "end_ms": 900,
                "cleaned_text": "忍辱勇猛大導師能於五濁不善世教化成熟惡眾生令彼修行得見佛",
                "timing_source": "chirp_word_timestamps",
            }
        ]
        canonical = {
            "body_text": "忍辱勇猛大導師能於五濁不善世教化成熟惡眾生令彼修行得見佛",
            "active_version": 1,
            "active_checksum": "scripture-test",
        }
        original = json.loads(json.dumps(display, ensure_ascii=False))
        result, meta = word_timed_scripture_verification_layer(
            display,
            canonical,
            minimum_contiguous_match_chars=8,
        )
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["timing_source"], "chirp_word_timestamps")
        self.assertEqual(result, original)

    def test_word_timed_mantra_uses_only_chirp_word_edges(self) -> None:
        words = [
            {
                "word": "啊",
                "start_ms": index * 200,
                "end_ms": index * 200 + 120,
            }
            for index in range(130)
        ]
        display = [
            {
                "segment_id": "title",
                "start_ms": 0,
                "end_ms": 920,
                "cleaned_text": "合念《彌勒根本大明神咒》：",
                "source_word_start_index": 0,
                "source_word_end_index": 4,
            },
            {
                "segment_id": "closing",
                "start_ms": 23_000,
                "end_ms": 24_000,
                "cleaned_text": "大眾請起立。",
                "source_word_start_index": 115,
                "source_word_end_index": 119,
            },
        ]
        canonical = {
            "title": "《得見彌勒根本大明神咒》",
            "body_text": MANTRA_BODY,
            "active_version": 1,
            "active_checksum": "mantra-test",
        }
        result, meta = word_timed_mantra_region_layer(
            display,
            MANTRA_BODY,
            canonical,
            {"words": words},
        )
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["cycle_count"], 1)
        mantra_cues = [
            item
            for item in result
            if str(item.get("segment_id") or "").startswith("mantra-word-")
        ]
        self.assertEqual(len(mantra_cues), 17)
        word_edges = {
            edge
            for word in words
            for edge in (word["start_ms"], word["end_ms"])
        }
        for cue in mantra_cues:
            self.assertIn(cue["start_ms"], word_edges)
            self.assertIn(cue["end_ms"], word_edges)
            self.assertEqual(cue["timing_source"], "chirp_word_boundary_projection")

    def test_review_lesson_without_merged_words_reports_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = self._data_dir(temp)
            result = review_lesson(
                srt_text=_srt([(0, 1200, "今天說法。")]),
                human_text="今天說法。",
                data_dir=data_dir,
                lesson_id="legacy",
                lesson_date="20260102",
            )
            self.assertTrue(result["human_semantic_display"]["legacy_fallback"])
            self.assertEqual(
                result["timing_policy"]["ordinary_speech"],
                "legacy_srt_cue_proportional_fallback",
            )
            self.assertFalse(
                result["timing_policy"]["chirp_word_timestamps_are_timing_truth"]
            )


if __name__ == "__main__":
    unittest.main()
