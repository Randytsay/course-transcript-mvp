from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.qa_report import base_chunk_density_reports
from app.providers.recover_chunk_hardened import empty_result_policy
from app.providers.targeted_patch import build_plan
from app.pipeline.dynamic_worker_hardened import (
    _record_targeted_patch_usage,
    _stored_qa_targeted_patch_seed,
)


class TargetedPatchTests(unittest.TestCase):
    def _job(self, root: Path, *, timing_repair: bool = True) -> Path:
        job = root / "job"
        (job / "chunks").mkdir(parents=True)
        plan = {
            "duration_seconds": 2400,
            "chunks": [
                {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 600_000},
                {"chunk_index": 1, "source_start_ms": 600_000, "source_end_ms": 1_200_000},
                {"chunk_index": 2, "source_start_ms": 1_200_000, "source_end_ms": 1_800_000},
                {"chunk_index": 3, "source_start_ms": 1_800_000, "source_end_ms": 2_400_000},
            ],
        }
        (job / "chunk-plan.json").write_text(json.dumps(plan), encoding="utf-8")

        repaired = {
            0: [{"word": "甲", "start_ms": 1_000, "end_ms": 599_000}],
            1: [
                {"word": "乙", "start_ms": 601_000, "end_ms": 720_000},
                {"word": "丙", "start_ms": 960_000, "end_ms": 1_199_000},
            ],
            2: [{"word": "丁", "start_ms": 1_201_000, "end_ms": 1_799_000}],
            3: [{"word": "戊", "start_ms": 1_801_000, "end_ms": 2_399_000}],
        }
        repairs = (
            [
                {
                    "chunk_index": 1,
                    "word_offset": 500,
                    "word": "野",
                    "original_start_ms": 710_000,
                    "original_end_ms": 970_000,
                    "repaired_start_ms": 710_000,
                    "repaired_end_ms": 720_000,
                    "reason": "provider_timing_outlier",
                }
            ]
            if timing_repair
            else []
        )
        (job / "pre-merge-words.json").write_text(
            json.dumps(
                {
                    "chunks": [
                        {"chunk_index": index, "words": words}
                        for index, words in repaired.items()
                    ],
                    "timing_repairs": repairs,
                }
            ),
            encoding="utf-8",
        )
        word_counts = {0: 2000, 1: 1200, 2: 2050, 3: 2010}
        for index, count in word_counts.items():
            directory = job / "chunks" / f"chunk-{index:03d}"
            directory.mkdir()
            (directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "chunk_index": index,
                        "role": "base",
                        "status": "SUCCEEDED",
                        "word_count": count,
                        "source_start_ms": plan["chunks"][index]["source_start_ms"],
                        "source_end_ms": plan["chunks"][index]["source_end_ms"],
                    }
                ),
                encoding="utf-8",
            )
            (directory / "words.json").write_text(
                json.dumps({"words": repaired[index]}),
                encoding="utf-8",
            )
        return job

    def test_plan_only_selects_high_confidence_timing_collapse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = self._job(Path(temporary))
            plan = build_plan(job, audibility_probe=lambda _start, _end: True)
            self.assertEqual(plan["status"], "planned")
            self.assertEqual(len(plan["items"]), 1)
            item = plan["items"][0]
            self.assertEqual(item["parent_chunk_index"], 1)
            self.assertEqual(item["role"], "patch")
            self.assertEqual(item["patch_mode"], "replace_window")
            self.assertEqual(
                item["reason"],
                "provider_timing_collapse_with_audible_zero_word_gap",
            )
            self.assertTrue(item["automatic_paid_retry"])
            self.assertEqual(item["gap_start_ms"], 720_000)
            self.assertEqual(item["gap_end_ms"], 960_000)
            self.assertEqual(item["source_start_ms"], 715_000)
            self.assertEqual(item["source_end_ms"], 965_000)
            self.assertLessEqual(item["duration_ms"], 360_000)

    def test_plan_does_not_auto_retry_low_density_without_timing_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = self._job(Path(temporary), timing_repair=False)
            plan = build_plan(job, audibility_probe=lambda _start, _end: True)
            self.assertEqual(plan["status"], "none")
            self.assertEqual(plan["items"], [])

    def test_empty_audible_result_is_success_only_for_patch(self) -> None:
        self.assertEqual(
            empty_result_policy({"role": "base"}, True),
            ("FAILED", None),
        )
        self.assertEqual(
            empty_result_policy({"role": "patch"}, True),
            ("SUCCEEDED", "audible_no_lexical_tokens"),
        )
        self.assertEqual(
            empty_result_policy({"role": "patch"}, False),
            ("EMPTY_SILENCE", None),
        )

    def test_completed_patch_with_zero_gap_words_resolves_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = self._job(Path(temporary))
            plan = build_plan(job, audibility_probe=lambda _start, _end: True)
            item = plan["items"][0]
            patch_index = item["patch_index"]
            (job / "chirp-targeted-patch-complete.json").write_text(
                json.dumps(
                    {
                        "verdicts": [
                            {
                                "patch_index": patch_index,
                                "parent_chunk_index": 1,
                                "status": "SUCCEEDED",
                                "word_count": 2,
                                "target_gap_word_count": 0,
                                "patch_verdict": None,
                                "operation_name": "operations/patch",
                            }
                        ],
                        "patch_decisions": [
                            {
                                "chunk_index": patch_index,
                                "applied": True,
                                "patch_words_inserted": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reports, _ = base_chunk_density_reports(
                job,
                2_400_000,
                audibility_probe=lambda _start, _end: True,
            )
            chunk = next(item for item in reports if item["chunk_index"] == 1)
            self.assertEqual(
                chunk["classification"],
                "targeted_patch_no_lexical_tokens",
            )
            self.assertTrue(chunk["review_required"])
            self.assertIn("do not auto-retry", chunk["recommended_action"])

    def test_stored_qa_seed_requires_audible_timing_collapse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            (job / "qa-report.json").write_text(
                json.dumps(
                    {
                        "density": {
                            "course_chunks": [
                                {
                                    "chunk_index": 7,
                                    "classification": "explained_by_audible_zero_word_gap",
                                    "timing_repair_count": 1,
                                    "zero_word_gaps": [
                                        {"start_ms": 1000, "end_ms": 61000, "audible": True}
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_stored_qa_targeted_patch_seed(job))
            payload = json.loads((job / "qa-report.json").read_text())
            payload["density"]["course_chunks"][0]["timing_repair_count"] = 0
            (job / "qa-report.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(_stored_qa_targeted_patch_seed(job))

    def test_targeted_patch_usage_has_separate_dedupe_key(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.calls = []

            def record_usage(self, **kwargs) -> None:
                self.calls.append(kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            job = data_dir / "jobs" / "job-1"
            job.mkdir(parents=True)
            (job / "chirp-targeted-patch-plan.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "patch_index": 900700,
                                "duration_ms": 60_000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (job / "chirp-targeted-patch-complete.json").write_text(
                json.dumps(
                    {
                        "verdicts": [
                            {
                                "patch_index": 900700,
                                "operation_name": "operations/patch-1",
                                "word_count": 123,
                                "target_gap_word_count": 100,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = FakeStore()
            _record_targeted_patch_usage(
                store,
                {"id": "job-1", "processing_strategy": "DYNAMIC_BATCHING"},
                data_dir=data_dir,
                worker_id="worker",
            )
            self.assertEqual(len(store.calls), 1)
            call = store.calls[0]
            self.assertEqual(call["dedupe_key"], "chirp-targeted-patch-900700")
            self.assertEqual(call["provider"], "google-cloud-speech")
            self.assertEqual(call["model"], "chirp_3")
            self.assertEqual(call["input_units"], 60)
            self.assertEqual(call["output_units"], 123)
            self.assertEqual(call["usage"]["role"], "patch")
            self.assertEqual(call["usage"]["target_gap_word_count"], 100)

    def test_completed_patch_with_gap_words_marks_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = self._job(Path(temporary))
            plan = build_plan(job, audibility_probe=lambda _start, _end: True)
            item = plan["items"][0]
            patch_index = item["patch_index"]
            (job / "chirp-targeted-patch-complete.json").write_text(
                json.dumps(
                    {
                        "verdicts": [
                            {
                                "patch_index": patch_index,
                                "parent_chunk_index": 1,
                                "status": "SUCCEEDED",
                                "word_count": 200,
                                "target_gap_word_count": 180,
                                "operation_name": "operations/patch",
                            }
                        ],
                        "patch_decisions": [
                            {
                                "chunk_index": patch_index,
                                "applied": True,
                                "patch_words_inserted": 200,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reports, _ = base_chunk_density_reports(
                job,
                2_400_000,
                audibility_probe=lambda _start, _end: True,
            )
            chunk = next(item for item in reports if item["chunk_index"] == 1)
            self.assertEqual(
                chunk["classification"],
                "repaired_by_targeted_patch",
            )
            self.assertFalse(chunk["review_required"])


if __name__ == "__main__":
    unittest.main()
