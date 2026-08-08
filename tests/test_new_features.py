from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NewFeatureTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
