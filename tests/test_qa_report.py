from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.qa_report import (
    base_chunk_density_reports,
    density_windows,
    patch_density_reports,
    patch_word_density,
    tail_coverage_assessment,
)
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

    def test_course_relative_density_explains_long_gap(self) -> None:
        segments = []
        for window in range(7):
            start = window * 900_000
            segments.append(
                {
                    "start_ms": start + 1000,
                    "end_ms": start + 899_000,
                    "raw_text": "字" * 3600,
                }
            )
        start = 7 * 900_000
        segments.extend(
            [
                {
                    "start_ms": start + 1000,
                    "end_ms": start + 250_000,
                    "raw_text": "字" * 1000,
                },
                {
                    "start_ms": start + 570_000,
                    "end_ms": start + 899_000,
                    "raw_text": "字" * 1200,
                },
            ]
        )
        windows, plans = density_windows(segments, 8 * 900_000)
        self.assertEqual(windows[7]["classification"], "explained_by_long_gap")
        self.assertIsNone(windows[7]["reason"])
        self.assertEqual(plans, [])

    def test_tail_residual_within_tolerance_does_not_mark_long_silence_audible(self) -> None:
        calls = []

        def probe(start_ms: int, end_ms: int) -> bool:
            calls.append((start_ms, end_ms))
            return len(calls) == 1

        report = tail_coverage_assessment(
            100_000,
            111_220,
            3000,
            audibility_probe=probe,
        )
        self.assertTrue(report["within_tolerance_audible"])
        self.assertFalse(report["beyond_tolerance_audible"])
        self.assertEqual(calls, [(100_000, 103_000), (103_000, 111_220)])

    def test_course_chunk_density_normalizes_short_final_chunk_and_explains_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            (job / "chunks").mkdir()
            plan = {
                "chunks": [
                    {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 600_000},
                    {"chunk_index": 1, "source_start_ms": 590_000, "source_end_ms": 1_190_000},
                    {"chunk_index": 2, "source_start_ms": 1_180_000, "source_end_ms": 1_780_000},
                    {"chunk_index": 3, "source_start_ms": 1_770_000, "source_end_ms": 2_130_000},
                ]
            }
            (job / "chunk-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            repaired_chunk_words = {
                0: [{"start_ms": 1000, "end_ms": 599_000}],
                1: [
                    {"start_ms": 591_000, "end_ms": 700_000},
                    {"start_ms": 940_000, "end_ms": 1_189_000},
                ],
                2: [{"start_ms": 1_181_000, "end_ms": 1_779_000}],
                3: [{"start_ms": 1_771_000, "end_ms": 2_129_000}],
            }
            (job / "pre-merge-words.json").write_text(
                json.dumps(
                    {
                        "chunks": [
                            {"chunk_index": index, "words": words}
                            for index, words in repaired_chunk_words.items()
                        ],
                        "timing_repairs": [
                            {
                                "chunk_index": 1,
                                "word_offset": 10,
                                "word": "測",
                                "reason": "provider_timing_outlier",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            specifications = {
                0: (2000, [{"start_ms": 1000, "end_ms": 599_000}]),
                1: (
                    1200,
                    [{"start_ms": 591_000, "end_ms": 1_189_000}],
                ),
                2: (2050, [{"start_ms": 1_181_000, "end_ms": 1_779_000}]),
                3: (1210, [{"start_ms": 1_771_000, "end_ms": 2_129_000}]),
            }
            for index, (count, words) in specifications.items():
                chunk = job / "chunks" / f"chunk-{index:03d}"
                chunk.mkdir()
                (chunk / "manifest.json").write_text(
                    json.dumps(
                        {
                            "chunk_index": index,
                            "role": "base",
                            "status": "SUCCEEDED",
                            "word_count": count,
                        }
                    ),
                    encoding="utf-8",
                )
                (chunk / "words.json").write_text(
                    json.dumps({"words": words}),
                    encoding="utf-8",
                )

            reports, plans = base_chunk_density_reports(
                job,
                2_130_000,
                audibility_probe=lambda _start, _end: True,
            )
            by_index = {item["chunk_index"]: item for item in reports}
            self.assertEqual(
                by_index[1]["classification"],
                "explained_by_audible_zero_word_gap",
            )
            self.assertTrue(by_index[1]["review_required"])
            self.assertEqual(by_index[1]["word_timeline_source"], "pre_merge_repaired")
            self.assertEqual(by_index[1]["timing_repair_count"], 1)
            self.assertGreater(by_index[1]["long_zero_word_gap_ms"], 200_000)
            self.assertEqual(by_index[3]["classification"], "normal")
            self.assertFalse(by_index[3]["review_required"])
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
