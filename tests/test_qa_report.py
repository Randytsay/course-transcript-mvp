from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.qa_report import patch_density_reports, patch_word_density
from app.providers.validate_outputs import _published_subtitle_count


class QaReportTests(unittest.TestCase):
    def test_patch_word_count_is_normalized_by_audio_window(self) -> None:
        report, plan = patch_word_density(
            {
                "chunk_index": 11,
                "status": "SUCCEEDED",
                "source_start_ms": 0,
                "source_end_ms": 174_000,
            },
            [{}] * 562,
            total_words=23_009,
            audio_ms=121_480,
        )
        self.assertAlmostEqual(float(report["words_per_minute"]), 193.79, places=2)
        self.assertEqual(report["review_required"], False)
        self.assertIsNone(plan)

    def test_impossible_patch_density_creates_review_plan(self) -> None:
        report, plan = patch_word_density(
            {
                "chunk_index": 99,
                "status": "SUCCEEDED",
                "source_start_ms": 0,
                "source_end_ms": 10_000,
            },
            [{}] * 1_000,
            total_words=1_000,
            audio_ms=60_000,
        )
        self.assertTrue(report["review_required"])
        self.assertIsNotNone(plan)
        self.assertEqual(plan["reason"], "patch_word_density_out_of_range")

    def test_patch_density_reports_only_patch_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            base = job / "chunks" / "chunk-000"
            patch = job / "chunks" / "chunk-011"
            base.mkdir(parents=True)
            patch.mkdir(parents=True)
            (base / "manifest.json").write_text(
                json.dumps({"role": "base", "status": "SUCCEEDED"}),
                encoding="utf-8",
            )
            (patch / "manifest.json").write_text(
                json.dumps(
                    {
                        "role": "patch",
                        "status": "SUCCEEDED",
                        "chunk_index": 11,
                        "source_start_ms": 0,
                        "source_end_ms": 174_000,
                    }
                ),
                encoding="utf-8",
            )
            (patch / "words.json").write_text(
                json.dumps({"words": [{}] * 562}),
                encoding="utf-8",
            )
            reports, plans = patch_density_reports(job, 23_009, 121_480)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["chunk"], "chunk-011")
            self.assertEqual(plans, [])


class ValidateOutputsTests(unittest.TestCase):
    def test_uses_display_layer_count_for_published_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "subtitles-cleaned.json"
            path.write_text(
                json.dumps({"segments": [{}, {}, {}], "display_segments": [{}, {}]}),
                encoding="utf-8",
            )
            self.assertEqual(_published_subtitle_count(path, 3), 2)

    def test_falls_back_to_raw_count_without_display_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "subtitles-cleaned.json"
            path.write_text(json.dumps({"segments": [{}, {}]}), encoding="utf-8")
            self.assertEqual(_published_subtitle_count(path, 3), 3)


if __name__ == "__main__":
    unittest.main()
