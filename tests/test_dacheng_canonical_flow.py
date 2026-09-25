from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.canonical.alignment import mantra_pair_display_layer, scripture_display_layer
from app.canonical.defaults import MANTRA_BODY, MANTRA_KEY, MANTRA_LINES, MANTRA_TITLE, SCRIPTURE_KEY
from app.canonical.lesson_context import (
    build_lesson_scripture_context,
    correction_reference_text,
    window_scripture_hint,
)
from app.canonical.store import CanonicalTextStore, active_canonical


def _segment(index: int, text: str, *, start_ms: int | None = None) -> dict[str, object]:
    start = index * 2000 if start_ms is None else start_ms
    return {
        "segment_id": f"seg-{index + 1:04d}",
        "start_ms": start,
        "end_ms": start + 1800,
        "raw_text": text,
        "corrected_text": text,
        "cleaned_text": text,
        "cleanup_actions": [],
        "cleanup_review_reasons": [],
    }


class CanonicalTextStoreTests(unittest.TestCase):
    def test_active_lookup_missing_db_is_read_only_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "missing-data-dir"
            self.assertIsNone(active_canonical(data_dir, SCRIPTURE_KEY))
            self.assertFalse(data_dir.exists())

    def test_active_lookup_old_db_without_canonical_tables_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            db_path = data_dir / "course-transcript.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE legacy_only(id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            before = db_path.read_bytes()
            self.assertIsNone(active_canonical(data_dir, SCRIPTURE_KEY))
            self.assertEqual(db_path.read_bytes(), before)

    def test_registry_seeds_mantra_but_requires_operator_scripture(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CanonicalTextStore(Path(temp) / "course-transcript.db")
            mantra = store.get_active(MANTRA_KEY)
            self.assertIsNotNone(mantra)
            assert mantra is not None
            self.assertEqual(mantra["active_version"], 1)
            self.assertEqual(mantra["body_text"], MANTRA_BODY)
            self.assertIsNone(store.get_active(SCRIPTURE_KEY))

            scripture = store.put_version(
                document_key=SCRIPTURE_KEY,
                title="《佛說彌勒大成佛經》",
                body_text="第一段正式經文。\n第二段正式經文。",
                actor="test:owner",
                note="authoritative fixture",
            )
            self.assertEqual(scripture["active_version"], 1)
            self.assertFalse(scripture["unchanged"])
            again = store.put_version(
                document_key=SCRIPTURE_KEY,
                title="《佛說彌勒大成佛經》",
                body_text="第一段正式經文。\n第二段正式經文。",
                actor="test:owner",
            )
            self.assertTrue(again["unchanged"])
            self.assertEqual(len(store.list_versions(SCRIPTURE_KEY)), 1)

            connection = sqlite3.connect(Path(temp) / "course-transcript.db")
            audit_count = connection.execute(
                "SELECT count(*) FROM canonical_document_audit WHERE document_key=?",
                (SCRIPTURE_KEY,),
            ).fetchone()[0]
            self.assertEqual(audit_count, 1)


class DachengCanonicalAlignmentTests(unittest.TestCase):
    def test_scripture_alignment_can_select_part_of_one_long_canonical_paragraph(self) -> None:
        clauses = [
            "爾時彌勒佛以大慈心語諸大眾言；",
            "汝等今者不以生天樂故；",
            "亦復不為今世樂故；",
            "來至我所；",
            "但為涅槃常樂因緣；",
            "是諸人等皆於佛法中種諸善根；",
            "釋迦牟尼佛出五濁世；",
            "種種呵責為汝說法；",
            "無奈汝何；",
            "教殖來緣；",
            "今得見我；",
            "我今攝受是諸人等。",
        ]
        canonical = {
            "title": "《佛說彌勒大成佛經》",
            "body_text": "".join(clauses),
            "active_version": 1,
            "active_checksum": "sha",
        }
        source = [
            _segment(index, clause.replace("涅槃", "涅盤"))
            for index, clause in enumerate(clauses)
        ]
        display, metadata = scripture_display_layer(source, canonical)
        self.assertTrue(metadata["applied"])
        self.assertGreaterEqual(
            metadata["canonical_line_end"],
            metadata["canonical_line_start"] + 2,
        )
        self.assertTrue(
            any("涅槃" in str(item.get("cleaned_text")) for item in display)
        )

    def test_lesson_context_tracks_recited_passage_and_current_explained_line(self) -> None:
        lines = [
            "第一句彌勒菩薩具足慈心利益一切眾生。",
            "第二句大眾一心合掌恭敬諦聽如來正法。",
            "第三句當來之世有慈氏尊出興於世。",
            "第四句聞其名號皆發無上菩提之心。",
            "第五句修習慈心三昧利益一切有情眾生。",
            "第六句如是功德不可思議諸佛皆共讚歎。",
        ]
        canonical = {
            "title": "《佛說彌勒大成佛經》",
            "body_text": "\n".join(lines),
            "active_version": 4,
            "active_checksum": "sha",
        }
        source = [_segment(index, line) for index, line in enumerate(lines)]
        context = build_lesson_scripture_context(source, canonical)
        self.assertTrue(context["applied"])
        self.assertEqual(context["canonical_line_start"], 1)
        self.assertEqual(context["canonical_line_end"], 6)
        lecture = [_segment(20, "這裡講到慈心三昧，就是修習慈心來利益一切有情眾生。")]
        hint = window_scripture_hint(lecture, context)
        self.assertTrue(hint["applied"])
        self.assertEqual(hint["canonical_line"], 5)
        reference = correction_reference_text(context, hint)
        self.assertIn("慈心三昧", reference)
        self.assertIn("Do not replace paraphrases", reference)

    def test_lesson_context_never_biases_unrelated_phonetic_text(self) -> None:
        canonical = {
            "title": "《佛說彌勒大成佛經》",
            "body_text": "\n".join(
                f"正式經文第{i}句內容必須有明確文字證據才能使用。" for i in range(1, 10)
            ),
            "active_version": 1,
            "active_checksum": "sha",
        }
        source = [_segment(index, f"一般生活課程內容只是聲音可能接近第{index}段") for index in range(10)]
        context = build_lesson_scripture_context(source, canonical)
        self.assertFalse(context["applied"])
        self.assertTrue(context["review_required"])

    def test_scripture_uses_ordered_exact_text_anchors_and_canonical_output(self) -> None:
        lines = [
            "如是我聞一時佛住於此為諸大眾宣說正法。",
            "爾時會中無量眾生一心合掌恭敬諦聽。",
            "世尊告諸比丘當來之世有大慈尊出興於世。",
            "其名彌勒具足相好光明遍照無量國土。",
            "一切眾生聞其名號皆發無上菩提之心。",
            "若有善男子善女人至心受持讀誦此經。",
            "當得遠離諸惡趣苦常生善處見佛聞法。",
            "復當修習慈心三昧利益一切有情眾生。",
            "如是功德不可思議諸佛世尊皆共讚歎。",
            "大眾聞佛所說歡喜奉行作禮而退。",
        ]
        canonical = {
            "title": "《佛說彌勒大成佛經》",
            "body_text": "\n".join(lines),
            "active_version": 3,
            "active_checksum": "scripture-sha",
        }
        source = [_segment(index, line) for index, line in enumerate(lines)]
        display, metadata = scripture_display_layer(source, canonical)
        self.assertTrue(metadata["applied"])
        self.assertEqual(metadata["match"], "ordered_exact_text_anchors")
        self.assertEqual(metadata["canonical_version"], 3)
        self.assertEqual(len(display), len(lines))
        self.assertEqual(str(display[0]["cleaned_text"]), lines[0])
        self.assertEqual(str(display[-1]["cleaned_text"]), lines[-1])

    def test_scripture_never_canonicalizes_from_unrelated_or_phonetic_like_text(self) -> None:
        canonical = {
            "title": "《佛說彌勒大成佛經》",
            "body_text": "\n".join(
                f"正式經文第{i}段內容完全依據後台文字不得臆測。" for i in range(1, 12)
            ),
            "active_version": 1,
            "active_checksum": "scripture-sha",
        }
        source = [
            _segment(index, f"大眾共誦聲音很接近但是辨識成不同的音近字第{index}段")
            for index in range(12)
        ]
        display, metadata = scripture_display_layer(source, canonical)
        self.assertFalse(metadata["applied"])
        self.assertEqual(metadata["reason"], "scripture_alignment_review")
        self.assertTrue(metadata["review_required"])
        self.assertEqual([item["segment_id"] for item in display], [item["segment_id"] for item in source])

    def test_mantra_folds_leader_and_congregation_pair_to_one_canonical_line(self) -> None:
        canonical = {
            "title": MANTRA_TITLE,
            "body_text": MANTRA_BODY,
            "active_version": 2,
            "active_checksum": "mantra-sha",
        }
        source: list[dict[str, object]] = [
            _segment(0, MANTRA_TITLE),
            _segment(1, MANTRA_TITLE),
        ]
        for line in MANTRA_LINES:
            source.append(_segment(len(source), line))  # leader
            source.append(_segment(len(source), line))  # congregation
        source.append(_segment(len(source), "大眾起立"))
        display, metadata = mantra_pair_display_layer(source, canonical)
        self.assertTrue(metadata["applied"])
        self.assertEqual(metadata["match"], "leader_congregation_structural_pair_cycles")
        self.assertEqual(metadata["input_pair_count"], len(MANTRA_LINES))
        self.assertEqual(metadata["output_line_count"], len(MANTRA_LINES))
        self.assertEqual(len(display), len(MANTRA_LINES) + 1)
        self.assertTrue(str(display[0]["cleaned_text"]).startswith(MANTRA_TITLE + "\n"))
        for item, line in zip(display[: len(MANTRA_LINES)], MANTRA_LINES):
            self.assertIn(line, str(item["cleaned_text"]))
        self.assertEqual(display[-1]["cleaned_text"], "大眾起立")

    def test_mantra_does_not_publish_when_pair_sequence_is_not_verified(self) -> None:
        canonical = {
            "title": MANTRA_TITLE,
            "body_text": MANTRA_BODY,
            "active_version": 1,
            "active_checksum": "mantra-sha",
        }
        source = [_segment(index, line) for index, line in enumerate(MANTRA_LINES)]
        display, metadata = mantra_pair_display_layer(source, canonical)
        self.assertFalse(metadata["applied"])
        self.assertEqual(metadata["reason"], "leader_congregation_pair_sequence_review")
        self.assertTrue(metadata["review_required"])
        self.assertEqual(len(display), len(source))


if __name__ == "__main__":
    unittest.main()
