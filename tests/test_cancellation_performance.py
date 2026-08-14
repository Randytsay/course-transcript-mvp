from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from app.jobs.cancellation import (
    cancel_chirp_operations,
    finalize_cancellation,
    request_cancellation,
)
from app.jobs.performance import (
    build_performance_summary,
    record_stage_completed,
    record_stage_started,
    write_performance_reports,
)
from app.jobs.store import JobStore


class CancellationPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.database_path = self.data_dir / "course-transcript.db"
        self.store = JobStore(self.database_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _approved_job(self, *, duration_seconds: float = 1800) -> dict:
        preview = self.store.create_preview(
            source_path="gdrive:課程/測試.mp3",
            source_name="測試.mp3",
            size_bytes=100,
            modified_at=None,
            mime_type="audio/mpeg",
            actor="owner@example.test",
        )
        job = self.store.create_preflight_job(
            preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="owner@example.test",
        )
        self.store.acquire_lease(job["id"], "preflight-worker")
        estimated = self.store.record_preflight_result(
            job_id=job["id"],
            duration_seconds=duration_seconds,
            source_checksum="a" * 64,
            media_format="mp3",
            audio_codec="mp3",
            estimated_cost_usd=Decimal("2.50"),
            pricing_version="test",
            worker_id="preflight-worker",
        )
        return self.store.approve_job(
            job_id=job["id"],
            expected_revision=estimated["revision"],
            confirmed_estimated_cost_usd=Decimal("2.50"),
            project_limit_usd=Decimal("200"),
            actor="owner@example.test",
        )

    def test_queued_job_cancels_immediately_and_cleans_temporary_audio(self) -> None:
        job = self._approved_job()
        job_dir = self.data_dir / "jobs" / job["id"]
        job_dir.mkdir(parents=True)
        (job_dir / "normalized.flac").write_bytes(b"audio")

        cancelled = request_cancellation(
            self.database_path,
            self.data_dir,
            job_id=job["id"],
            expected_revision=job["revision"],
            reason="選錯來源檔",
            cleanup_mode="temporary",
            actor="owner@example.test",
        )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(Decimal(cancelled["reserved_cost_usd"]), Decimal("0"))
        self.assertFalse((job_dir / "normalized.flac").exists())
        events = self.store.list_job_events(job["id"])
        self.assertEqual(events[0]["event_type"], "job_cancelled")

    def test_running_job_enters_cancelling_then_finalizes(self) -> None:
        job = self._approved_job()
        self.store.acquire_lease(job["id"], "pipeline-worker")
        running = self.store.begin_stage(
            job_id=job["id"],
            stage="chirp",
            status="transcribing",
            detail="test",
            progress=21,
            input_checksum="a" * 64,
            worker_id="pipeline-worker",
        )
        cancelling = request_cancellation(
            self.database_path,
            self.data_dir,
            job_id=job["id"],
            expected_revision=running["revision"],
            reason="停止測試",
            cleanup_mode="preserve",
            actor="owner@example.test",
        )
        self.assertEqual(cancelling["status"], "cancelling")

        cancelled = finalize_cancellation(
            self.database_path,
            self.data_dir,
            job_id=job["id"],
            worker_id="pipeline-worker",
            provider_results=[{"chunk": "chunk-000", "outcome": "requested"}],
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["locked_by"])

    @patch("google.cloud.speech_v2.SpeechClient")
    def test_provider_cancellation_is_best_effort(self, speech_client: Mock) -> None:
        job = self._approved_job()
        job_dir = self.data_dir / "jobs" / job["id"]
        chunk_dir = job_dir / "chunks" / "chunk-000"
        chunk_dir.mkdir(parents=True)
        (chunk_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "chunk_index": 0,
                    "status": "SUBMITTED",
                    "operation_name": "projects/p/locations/us/operations/123",
                }
            ),
            encoding="utf-8",
        )
        client = speech_client.return_value

        results = cancel_chirp_operations(job_dir)

        self.assertEqual(results[0]["outcome"], "requested")
        client.cancel_operation.assert_called_once()
        manifest = json.loads((chunk_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cancel_request_outcome"], "requested")

    def test_performance_summary_contains_stage_chunk_and_gemini_metrics(self) -> None:
        job = self._approved_job(duration_seconds=1800)
        job_dir = self.data_dir / "jobs" / job["id"]
        chunk_dir = job_dir / "chunks" / "chunk-000"
        correction_dir = job_dir / "correction-v2"
        glossary_dir = job_dir / "glossary"
        chunk_dir.mkdir(parents=True)
        correction_dir.mkdir(parents=True)
        glossary_dir.mkdir(parents=True)
        (job_dir / "chunk-plan.json").write_text(
            json.dumps(
                {
                    "chunks": [
                        {
                            "chunk_index": 0,
                            "source_start_ms": 0,
                            "source_end_ms": 900000,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (chunk_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "chunk_index": 0,
                    "status": "SUCCEEDED",
                    "attempt_count": 2,
                    "submitted_at": "2026-08-01T00:00:00+00:00",
                    "provider_completed_at": "2026-08-01T00:05:00+00:00",
                    "recovered_at": "2026-08-01T00:05:20+00:00",
                    "word_count": 3210,
                }
            ),
            encoding="utf-8",
        )
        (glossary_dir / "global-terms.json").write_text(
            json.dumps(
                {
                    "model": "gemini-3.7-flash",
                    "request_started_at": "2026-08-01T00:06:00+00:00",
                    "response_completed_at": "2026-08-01T00:06:10+00:00",
                    "attempt_count": 1,
                    "usage_metadata": {
                        "prompt_token_count": 1000,
                        "candidates_token_count": 100,
                    },
                }
            ),
            encoding="utf-8",
        )
        (correction_dir / "segment-1.json").write_text(
            json.dumps(
                {
                    "model": "gemini-3.7-flash",
                    "source_start_ms": 0,
                    "source_end_ms": 30000,
                    "request_started_at": "2026-08-01T00:06:10+00:00",
                    "response_completed_at": "2026-08-01T00:06:25+00:00",
                    "attempt_count": 2,
                    "usage_metadata": {
                        "prompt_token_count": 2000,
                        "candidates_token_count": 500,
                    },
                }
            ),
            encoding="utf-8",
        )
        record_stage_started(self.database_path, job["id"], "chirp")
        record_stage_completed(self.database_path, job["id"], "chirp")

        summary = build_performance_summary(
            self.database_path,
            self.data_dir,
            job["id"],
        )

        self.assertEqual(len(summary["chunks"]), 1)
        self.assertEqual(summary["chunks"][0]["providerProcessingMs"], 300000)
        self.assertEqual(summary["chunks"][0]["recoveryDelayMs"], 20000)
        self.assertEqual(len(summary["geminiCalls"]), 2)
        self.assertEqual(
            sum(item["inputTokens"] for item in summary["geminiCalls"]),
            3000,
        )
        self.assertEqual(summary["stageAttempts"][0]["stage"], "chirp")
        paths = write_performance_reports(job_dir, summary)
        self.assertTrue(all(path.exists() for path in paths.values()))


if __name__ == "__main__":
    unittest.main()
