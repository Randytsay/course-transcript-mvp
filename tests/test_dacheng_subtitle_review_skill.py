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


if __name__ == "__main__":
    unittest.main()
