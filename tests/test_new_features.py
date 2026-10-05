from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NewFeatureTests(unittest.TestCase):
    def test_processing_strategy_changes_estimate_and_is_persisted(self) -> None:
        from app.jobs.costs import CostConfig, estimate_job_cost
        from app.jobs.strategy import DYNAMIC_BATCHING, STANDARD_BATCH
        from app.jobs.store import JobStore

        dynamic = estimate_job_cost(
            900,
            CostConfig().for_processing_strategy(DYNAMIC_BATCHING),
        )
        standard = estimate_job_cost(
            900,
            CostConfig().for_processing_strategy(STANDARD_BATCH),
        )
        self.assertLess(dynamic.chirp_usd, standard.chirp_usd)
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(Path(temp) / "course-transcript.db")
            preview = store.create_preview(
                source_path="gdrive:課程/急件.mp3",
                source_name="急件.mp3",
                size_bytes=1024,
                modified_at=None,
                mime_type="audio/mp3",
                actor="test-user",
            )
            job = store.create_preflight_job(
                preview_id=preview["id"],
                language_code="zh-TW",
                profile="highest_accuracy",
                enable_gemini_correction=True,
                enable_subtitles=True,
                require_human_review=True,
                processing_strategy=STANDARD_BATCH,
                actor="test-user",
            )
            self.assertEqual(job["processing_strategy"], STANDARD_BATCH)

    def test_output_compatibility_and_production_filter(self) -> None:
        from app.jobs.exports import normalize_output_formats, production_output_formats

        self.assertEqual(normalize_output_formats(None), ["srt", "txt", "csv"])
        self.assertEqual(
            normalize_output_formats(["srt", "docx", "pdf", "txt"]),
            ["srt", "docx", "pdf", "txt"],
        )
        self.assertEqual(
            production_output_formats(["srt", "docx", "pdf", "txt"]),
            ["srt", "txt"],
        )

    def test_dynamic_chunk_plan_uses_uniform_first_chunk(self) -> None:
        module_name = "app.providers.run_chirp_pipeline"
        with patch.dict(os.environ, {"CHIRP_DYNAMIC_BATCHING": "true"}):
            sys.modules.pop(module_name, None)
            module = importlib.import_module(module_name)
        plan = module.compute_chunk_plan(2_000)
        self.assertEqual(plan[0], (0, 0.0, 900.0))
        self.assertEqual(plan[1][1], 890.0)

    def test_speech_dependency_exposes_dynamic_batching_strategy(self) -> None:
        from google.cloud.speech_v2.types import cloud_speech

        strategy = cloud_speech.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING
        request = cloud_speech.BatchRecognizeRequest(processing_strategy=strategy)
        self.assertEqual(request.processing_strategy, strategy)

    def test_dynamic_queue_recovers_expired_lease(self) -> None:
        from app.pipeline.dynamic_state import next_waiting_dynamic

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE jobs(
                id TEXT, status TEXT, active_stage TEXT, approved_at TEXT,
                locked_by TEXT, lease_expires_at TEXT, updated_at TEXT,
                created_at TEXT, batch_id TEXT, queue_position INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO jobs VALUES(
                'expired-job', 'transcribing', 'chirp', '2026-08-01T00:00:00+00:00',
                'dead-worker', '2020-01-01T00:00:00+00:00',
                '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00', NULL, 0
            )
            """
        )

        class Store:
            def connect(self):
                return connection

        selected = next_waiting_dynamic(Store())
        self.assertIsNotNone(selected)
        self.assertEqual(selected["id"], "expired-job")
        connection.close()

    def test_safe_drive_publish_backs_up_existing_file(self) -> None:
        from app.jobs.drive_publish import publish_outputs

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp) / "job-123"
            job.mkdir()
            local = job / "subtitles-corrected.srt"
            local.write_text("new subtitle", encoding="utf-8")
            remote: dict[str, bytes] = {"gdrive:course/lesson.srt": b"old subtitle"}

            def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
                operation = command[1]
                if operation == "size":
                    path = command[-1]
                    if path not in remote:
                        return subprocess.CompletedProcess(command, 1, "", "object not found")
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({"count": 1, "bytes": len(remote[path])}),
                        "",
                    )
                if operation == "copyto":
                    source, destination = command[-2], command[-1]
                    remote[destination] = Path(source).read_bytes()
                    return subprocess.CompletedProcess(command, 0, "", "")
                if operation == "moveto":
                    source, destination = command[-2], command[-1]
                    if source not in remote:
                        return subprocess.CompletedProcess(command, 1, "", "object not found")
                    remote[destination] = remote.pop(source)
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError(command)

            state = publish_outputs(
                job,
                source_name="lesson.mp3",
                destination="gdrive:course",
                output_formats=["srt"],
                authorized=True,
                runner=runner,
                sleeper=lambda _: None,
                jitter=lambda: 0,
                clock=lambda: 100,
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["backup_count"], 1)
            self.assertEqual(remote["gdrive:course/lesson.srt"], b"new subtitle")
            backup = state["files"]["srt"]["backup_remote_path"]
            self.assertEqual(remote[backup], b"old subtitle")

    def test_retry_failed_stage_with_chunk_index_and_force(self) -> None:
        from app.jobs.store import JobStore

        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "course-transcript.db"
            store = JobStore(db_path)
            preview = store.create_preview(
                source_path="/data/source.mp3",
                source_name="source.mp3",
                size_bytes=1024,
                modified_at=None,
                mime_type="audio/mp3",
                actor="test-user",
            )
            job = store.create_preflight_job(
                preview_id=preview["id"],
                actor="test-user",
                language_code="zh-TW",
                profile="standard",
                enable_gemini_correction=True,
                enable_subtitles=True,
                require_human_review=False,
            )
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE jobs SET status = 'completed', active_stage = 'chirp', approved_at = '2026-08-01T00:00:00+00:00', reserved_cost_usd = '1.00' WHERE id = ?",
                    (job["id"],),
                )

            job_dir = Path(temp) / "jobs" / job["id"]
            chunk_manifest = job_dir / "chunks" / "chunk-001" / "manifest.json"
            chunk_manifest.parent.mkdir(parents=True, exist_ok=True)
            (job_dir / "chunk-plan.json").write_text(
                json.dumps({"chunks": [{"chunk_index": 1}]}),
                encoding="utf-8",
            )
            chunk_manifest.write_text('{"status": "SUCCEEDED"}', encoding="utf-8")
            (chunk_manifest.parent / "chirp-raw.json").write_text("old raw", encoding="utf-8")
            (job_dir / "merged-words.json").write_text("old merged", encoding="utf-8")
            (job_dir / "subtitles-corrected.json").write_text("old subtitles", encoding="utf-8")

            res = store.retry_failed_stage(
                job_id=job["id"],
                expected_revision=job["revision"],
                stage="chirp",
                chunk_index=1,
                force=True,
                actor="test-user",
            )
            self.assertEqual(res["status"], "transcribing")
            self.assertEqual(res["active_stage"], "chirp")
            self.assertEqual(res["stage_detail"], "重新辨識第 2 段")
            self.assertFalse(chunk_manifest.exists())
            request = json.loads((job_dir / "chirp-retry-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["chunks"][0]["chunk_index"], 1)
            archive = job_dir / request["chunks"][0]["archive"]
            self.assertEqual((archive / "merged-words.json").read_text(encoding="utf-8"), "old merged")
            self.assertEqual((archive / "subtitles-corrected.json").read_text(encoding="utf-8"), "old subtitles")
            attempt = chunk_manifest.parent / "attempts" / request["chunks"][0]["archive"].split("/")[-1]
            self.assertEqual((attempt / "manifest.json").read_text(encoding="utf-8"), '{"status": "SUCCEEDED"}')
            self.assertEqual((attempt / "chirp-raw.json").read_text(encoding="utf-8"), "old raw")

            # A requested re-recognition must be routed to submission, not the
            # normal recovery pass that only polls an existing provider operation.
            (job_dir / "chirp-submitted.json").write_text("{}", encoding="utf-8")
            from app.pipeline import dynamic_worker_hardened as worker

            self.assertIsNone(worker._next_due_waiting(store, Path(temp)))
            resumable = worker._next_resumable(store, Path(temp))
            self.assertEqual(resumable["id"], job["id"])
            worker._clear_chunk_retry_request(job_dir)
            self.assertEqual(worker._next_due_waiting(store, Path(temp))["id"], job["id"])

    def test_targeted_patch_due_route_preempts_base_recovery(self) -> None:
        from app.jobs.store import JobStore
        from app.pipeline import dynamic_worker_hardened as worker

        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            store = JobStore(data_dir / "course-transcript.db")
            preview = store.create_preview(
                source_path="/data/source.mp3",
                source_name="source.mp3",
                size_bytes=1024,
                modified_at=None,
                mime_type="audio/mp3",
                actor="test-user",
            )
            job = store.create_preflight_job(
                preview_id=preview["id"],
                actor="test-user",
                language_code="zh-TW",
                profile="standard",
                enable_gemini_correction=True,
                enable_subtitles=True,
                require_human_review=True,
            )
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE jobs SET status='transcribing', active_stage='chirp', approved_at='2026-08-01T00:00:00+00:00', reserved_cost_usd='1.00' WHERE id=?",
                    (job["id"],),
                )
            job_dir = data_dir / "jobs" / job["id"]
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "chirp-submitted.json").write_text("{}", encoding="utf-8")
            (job_dir / "chirp-targeted-patch-submitted.json").write_text(
                json.dumps({"patch_count": 1, "patch_indices": [900700]}),
                encoding="utf-8",
            )
            (job_dir / "chirp-recovery-state.json").write_text(
                json.dumps(
                    {
                        "last_outcome": "pending",
                        "last_detail": "targeted_patch_submitted",
                        "last_checked_at": "2020-01-01T00:00:00+00:00",
                        "transient_errors": 0,
                        "next_recovery_at": "2020-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            targeted = worker._next_due_targeted_patch(store, data_dir)
            self.assertIsNotNone(targeted)
            self.assertEqual(targeted["id"], job["id"])
            self.assertIsNone(worker._next_due_waiting(store, data_dir))
            # Generic resumable routing must not re-enter the submit path while
            # a targeted patch is in flight; only the due targeted recovery
            # selector may own it.
            self.assertIsNone(worker._next_resumable(store, data_dir))

    def test_retry_chunk_uses_standard_without_invalidating_retained_dynamic(self) -> None:
        from app.providers import run_chirp_pipeline_hardened as hardened

        with tempfile.TemporaryDirectory() as temp:
            job_dir = Path(temp)
            chunks_dir = job_dir / "chunks"
            retained = chunks_dir / "chunk-000"
            retained.mkdir(parents=True)
            (retained / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "processing_strategy": "DYNAMIC_BATCHING",
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "chirp-retry-request.json").write_text(
                json.dumps({"chunks": [{"chunk_index": 1}]}),
                encoding="utf-8",
            )
            with (
                patch.object(hardened.base, "JOB", job_dir),
                patch.object(hardened.base, "CHUNKS", chunks_dir),
                patch.object(hardened.base, "DYNAMIC_BATCHING", True),
            ):
                self.assertTrue(hardened._chunk_dynamic_batching(0))
                self.assertFalse(hardened._chunk_dynamic_batching(1))
                retry_dir = chunks_dir / "chunk-001"
                retry_dir.mkdir(parents=True)
                (retry_dir / "manifest.json").write_text(
                    json.dumps(
                        {
                            "status": "SUBMITTED",
                            "processing_strategy": "PROCESSING_STRATEGY_UNSPECIFIED",
                        }
                    ),
                    encoding="utf-8",
                )
                (job_dir / "chirp-retry-request.json").unlink()
                self.assertFalse(hardened._chunk_dynamic_batching(1))

    def test_targeted_patch_budget_uses_standard_batch_rate(self) -> None:
        from app.pipeline import dynamic_worker_hardened as worker

        record = {
            "id": "job-1",
            "processing_strategy": "DYNAMIC_BATCHING",
            "reserved_cost_usd": "1.0000",
        }
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(worker, "estimated_accrued_cost", return_value=Decimal("0")),
                patch.dict(os.environ, {}, clear=True),
            ):
                allowed, accrued, extra, reserved = worker._targeted_patch_budget(
                    record,
                    data_dir=Path(temp),
                    plan={"total_duration_ms": 60_000},
                )
        self.assertTrue(allowed)
        self.assertEqual(accrued, Decimal("0"))
        self.assertEqual(extra, Decimal("0.0160"))
        self.assertEqual(reserved, Decimal("1.0000"))

    def test_density_only_completeness_signal_is_nonblocking(self) -> None:
        from app.providers.chirp_completeness_gate import evaluate

        with tempfile.TemporaryDirectory() as temp:
            job_dir = Path(temp)
            segments = [
                {
                    "segment_id": "seg-0001",
                    "start_ms": 0,
                    "end_ms": 1500,
                    "raw_text": "第一句",
                    "text": "第一句",
                },
                {
                    "segment_id": "seg-0002",
                    "start_ms": 1700,
                    "end_ms": 3200,
                    "raw_text": "第二句",
                    "text": "第二句",
                },
            ]
            (job_dir / "subtitles.json").write_text(
                json.dumps({"segments": segments}, ensure_ascii=False),
                encoding="utf-8",
            )
            (job_dir / "merged-words.json").write_text(
                json.dumps({"words": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (job_dir / "chunk-plan.json").write_text(
                json.dumps({"duration_seconds": 3.2, "chunks": []}),
                encoding="utf-8",
            )
            with (
                patch(
                    "app.providers.chirp_completeness_gate.base_chunk_density_reports",
                    return_value=(
                        [],
                        [{"chunk_index": 0, "classification": "density_out_of_range"}],
                    ),
                ),
                patch(
                    "app.providers.chirp_completeness_gate.density_windows",
                    return_value=(
                        [],
                        [{"start_ms": 0, "end_ms": 1000, "classification": "density_out_of_range"}],
                    ),
                ),
            ):
                report = evaluate(
                    job_dir,
                    audibility_probe=lambda _start, _end: False,
                )
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["handoff_allowed"])
        self.assertEqual(report["summary"]["blocker_count"], 0)
        reasons = {item["reason"] for item in report["warnings"]}
        self.assertIn("course_relative_chunk_density_review", reasons)
        self.assertIn("course_density_window_review", reasons)

    def test_merge_accepts_failed_base_only_when_patches_fully_cover_it(self) -> None:
        from app.providers import merge_chunks

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            chunks = root / "chunks"
            chunks.mkdir()
            base_dir = chunks / "chunk-000"
            base_dir.mkdir()
            (base_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "chunk_index": 0,
                        "role": "repair",
                        "status": "FAILED",
                        "source_start_ms": 0,
                        "source_end_ms": 900000,
                    }
                ),
                encoding="utf-8",
            )
            for index, (start, end) in enumerate(
                [(0, 300000), (300000, 600000), (600000, 900000)],
                start=940001,
            ):
                directory = chunks / f"chunk-{index:03d}"
                directory.mkdir()
                (directory / "manifest.json").write_text(
                    json.dumps(
                        {
                            "chunk_index": index,
                            "role": "patch",
                            "patch_mode": "replace_window",
                            "status": "SUCCEEDED",
                            "source_start_ms": start,
                            "source_end_ms": end,
                        }
                    ),
                    encoding="utf-8",
                )
                (directory / "words.json").write_text(
                    json.dumps(
                        {
                            "words": [
                                {
                                    "word": "測",
                                    "start_ms": start + 1000,
                                    "end_ms": start + 1100,
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
            with patch.object(merge_chunks, "CHUNKS", chunks):
                loaded = merge_chunks.load_chunks()
            base = next(item for item in loaded if item[0]["chunk_index"] == 0)
            self.assertEqual(base[1], [])
            self.assertTrue(base[0]["derived_reconstructed_from_patches"])

    def test_merge_rejects_failed_base_when_patch_coverage_has_gap(self) -> None:
        from app.providers import merge_chunks

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            chunks = root / "chunks"
            chunks.mkdir()
            base_dir = chunks / "chunk-000"
            base_dir.mkdir()
            (base_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "chunk_index": 0,
                        "role": "base",
                        "status": "FAILED",
                        "source_start_ms": 0,
                        "source_end_ms": 900000,
                    }
                ),
                encoding="utf-8",
            )
            for index, (start, end) in enumerate(
                [(0, 300000), (600000, 900000)],
                start=940001,
            ):
                directory = chunks / f"chunk-{index:03d}"
                directory.mkdir()
                (directory / "manifest.json").write_text(
                    json.dumps(
                        {
                            "chunk_index": index,
                            "role": "patch",
                            "patch_mode": "replace_window",
                            "status": "SUCCEEDED",
                            "source_start_ms": start,
                            "source_end_ms": end,
                        }
                    ),
                    encoding="utf-8",
                )
                (directory / "words.json").write_text(
                    json.dumps({"words": []}),
                    encoding="utf-8",
                )
            with patch.object(merge_chunks, "CHUNKS", chunks):
                with self.assertRaisesRegex(RuntimeError, "chunk-000"):
                    merge_chunks.load_chunks()

    def test_completeness_repair_defers_extra_windows_instead_of_blocking_round(self) -> None:
        from app.providers.chirp_completeness_gate import build_auto_repair_patch_plan

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "chunk-plan.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 1200,
                        "chunks": [
                            {
                                "chunk_index": 0,
                                "source_start_ms": 0,
                                "source_end_ms": 1200000,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = {
                "blockers": [
                    {
                        "reason": "audible_subtitle_gap",
                        "gap_start_ms": 10000,
                        "gap_end_ms": 260000,
                    },
                    {
                        "reason": "audible_subtitle_gap",
                        "gap_start_ms": 400000,
                        "gap_end_ms": 650000,
                    },
                    {
                        "reason": "audible_subtitle_gap",
                        "gap_start_ms": 800000,
                        "gap_end_ms": 1050000,
                    },
                ]
            }
            plan = build_auto_repair_patch_plan(
                job,
                report,
                context_ms=0,
                max_total_ms=600000,
            )
        self.assertEqual(plan["status"], "planned")
        self.assertIsNone(plan["auto_submit_blocked_reason"])
        self.assertEqual(plan["proposed_patch_count"], 2)
        self.assertEqual(plan["total_duration_ms"], 500000)
        self.assertEqual(plan["deferred_patch_count"], 1)
        self.assertEqual(plan["original_proposed_patch_count"], 3)

    def test_production_recheck_resumes_from_local_merged_words(self) -> None:
        from app.pipeline.dynamic_worker_production import _resume_from_local_evidence

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "merged-words.json").write_text(
                json.dumps({"words": []}),
                encoding="utf-8",
            )
            (job / "chirp-completeness-recheck-request.json").write_text(
                json.dumps({"force_gate_rerun": True}),
                encoding="utf-8",
            )
            self.assertTrue(
                _resume_from_local_evidence({"active_stage": "segment"}, job)
            )
            (job / "chirp-completeness-recheck-request.json").unlink()
            self.assertTrue(
                _resume_from_local_evidence(
                    {"active_stage": "chirp_completeness"},
                    job,
                )
            )
            self.assertTrue(
                _resume_from_local_evidence({"active_stage": "validation"}, job)
            )
            self.assertTrue(
                _resume_from_local_evidence({"active_stage": "qa"}, job)
            )
            self.assertFalse(
                _resume_from_local_evidence({"active_stage": "chirp"}, job)
            )

    def test_legacy_zero_count_duration_block_is_replanned_and_preserved(self) -> None:
        from app.pipeline import dynamic_worker_hardened as worker

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "chirp-completeness-auto-repair.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "policy": "chirp_completeness_auto_repair_v1",
                        "provider_calls_started": False,
                        "round": 1,
                        "max_rounds": 3,
                        "proposed_patch_count": 0,
                        "total_duration_ms": 0,
                        "blocked_reason": "repair_duration_cap_exceeded",
                    }
                ),
                encoding="utf-8",
            )
            fake_plan = {
                "status": "planned",
                "items": [
                    {
                        "patch_index": 920001,
                        "duration_ms": 120000,
                    }
                ],
                "proposed_patch_count": 1,
                "total_duration_ms": 120000,
                "auto_submit_blocked_reason": None,
            }
            with patch.object(
                worker,
                "build_auto_repair_patch_plan",
                return_value=fake_plan,
            ):
                plan = worker._prepare_chirp_completeness_auto_repair(
                    job,
                    {"blockers": [{"reason": "audible_subtitle_gap"}]},
                )
            self.assertIsNotNone(plan)
            marker = json.loads(
                (job / "chirp-completeness-auto-repair.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(marker["replanned_from_legacy_block"])
            self.assertEqual(
                marker["legacy_block_snapshot"]["blocked_reason"],
                "repair_duration_cap_exceeded",
            )

    def test_legacy_block_replan_is_attempted_only_once_if_still_blocked(self) -> None:
        from app.pipeline import dynamic_worker_hardened as worker

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "chirp-completeness-auto-repair.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "policy": "chirp_completeness_auto_repair_v1",
                        "provider_calls_started": False,
                        "round": 1,
                        "max_rounds": 3,
                        "proposed_patch_count": 0,
                        "total_duration_ms": 0,
                        "blocked_reason": "repair_duration_cap_exceeded",
                    }
                ),
                encoding="utf-8",
            )
            blocked_plan = {
                "status": "blocked",
                "items": [],
                "proposed_patch_count": 0,
                "total_duration_ms": 0,
                "auto_submit_blocked_reason": "repair_duration_cap_exceeded",
            }
            with patch.object(
                worker,
                "build_auto_repair_patch_plan",
                return_value=blocked_plan,
            ) as planner:
                first = worker._prepare_chirp_completeness_auto_repair(
                    job,
                    {"blockers": [{"reason": "audible_subtitle_gap"}]},
                )
                second = worker._prepare_chirp_completeness_auto_repair(
                    job,
                    {"blockers": [{"reason": "audible_subtitle_gap"}]},
                )
            self.assertIsNone(first)
            self.assertIsNone(second)
            self.assertEqual(planner.call_count, 1)
            marker = json.loads(
                (job / "chirp-completeness-auto-repair.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(marker["legacy_replan_attempted"])
            self.assertEqual(
                marker["legacy_block_snapshot"]["blocked_reason"],
                "repair_duration_cap_exceeded",
            )

    def test_completeness_repair_defers_excess_patch_count(self) -> None:
        from app.providers.chirp_completeness_gate import build_auto_repair_patch_plan

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "chunk-plan.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 300,
                        "chunks": [
                            {
                                "chunk_index": 0,
                                "source_start_ms": 0,
                                "source_end_ms": 300000,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            blockers = []
            for index in range(15):
                start = index * 15000
                blockers.append(
                    {
                        "reason": "audible_subtitle_gap",
                        "gap_start_ms": start,
                        "gap_end_ms": start + 5000,
                    }
                )
            plan = build_auto_repair_patch_plan(
                job,
                {"blockers": blockers},
                context_ms=0,
                merge_gap_ms=0,
                max_total_ms=600000,
                max_patches=12,
            )
        self.assertEqual(plan["status"], "planned")
        self.assertIsNone(plan["auto_submit_blocked_reason"])
        self.assertEqual(plan["proposed_patch_count"], 12)
        self.assertEqual(plan["deferred_patch_count"], 3)
        self.assertEqual(plan["original_proposed_patch_count"], 15)

    def test_long_single_gap_is_split_into_bounded_windows(self) -> None:
        from app.providers.chirp_completeness_gate import build_auto_repair_patch_plan

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "chunk-plan.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 900,
                        "chunks": [
                            {
                                "chunk_index": 0,
                                "source_start_ms": 0,
                                "source_end_ms": 900000,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            plan = build_auto_repair_patch_plan(
                job,
                {
                    "blockers": [
                        {
                            "reason": "audible_subtitle_gap",
                            "gap_start_ms": 280720,
                            "gap_end_ms": 895160,
                        }
                    ]
                },
                context_ms=5000,
                max_total_ms=600000,
                max_window_ms=300000,
            )
        self.assertEqual(plan["status"], "planned")
        self.assertIsNone(plan["auto_submit_blocked_reason"])
        self.assertEqual(plan["proposed_patch_count"], 2)
        self.assertEqual(plan["total_duration_ms"], 600000)
        self.assertEqual(plan["deferred_patch_count"], 1)
        self.assertTrue(all(item["duration_ms"] <= 300000 for item in plan["items"]))
        self.assertTrue(all(item["split_from_long_window"] for item in plan["items"]))



if __name__ == "__main__":
    unittest.main()
