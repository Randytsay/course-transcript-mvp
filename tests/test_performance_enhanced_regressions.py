from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.jobs.performance import record_stage_completed, record_stage_started
from app.jobs.performance_enhanced import build_performance_summary
from app.jobs.store import JobStore


class EnhancedPerformanceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.database_path = self.data_dir / "course-transcript.db"
        self.store = JobStore(self.database_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _approved_job(self, *, duration_seconds: float = 1800) -> dict:
        preview = self.store.create_preview(
            source_path="gdrive:課程/效能測試.mp3",
            source_name="效能測試.mp3",
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

    def test_superseded_legacy_running_attempt_is_excluded_from_effective_duration(self) -> None:
        job = self._approved_job()
        record_stage_started(self.database_path, job["id"], "download")
        record_stage_started(self.database_path, job["id"], "download")
        record_stage_completed(self.database_path, job["id"], "download")

        # Reproduce the production symptom: attempt 1 was left running by an
        # older worker and appears to have started long before attempt 2.
        with self.store.transaction() as connection:
            connection.execute(
                """
                UPDATE performance_stage_attempts
                SET started_at = ?, completed_at = NULL,
                    active_duration_ms = NULL, status = 'running'
                WHERE job_id = ? AND stage = 'download' AND attempt_number = 1
                """,
                ("2026-08-16T00:00:00+00:00", job["id"]),
            )

        summary = build_performance_summary(
            self.database_path,
            self.data_dir,
            job["id"],
        )

        self.assertEqual(summary["staleStageAttemptCount"], 1)
        first = summary["stageAttempts"][0]
        self.assertEqual(first["reportingStatus"], "superseded_unclosed")
        self.assertTrue(first["excludedFromEffectiveDuration"])
        self.assertEqual(first["activeDurationMs"], 0)
        self.assertGreater(first["observedActiveDurationMs"], 0)
        download_total = next(
            item for item in summary["stageTotals"] if item["stage"] == "download"
        )
        self.assertLess(download_total["durationMs"], first["observedActiveDurationMs"])

    def test_minimax_retry_is_not_reported_as_gemini_retry(self) -> None:
        job = self._approved_job(duration_seconds=600)
        job_dir = self.data_dir / "jobs" / job["id"]
        gemini_dir = job_dir / "correction-v2"
        minimax_dir = job_dir / "correction-m3-v1"
        gemini_dir.mkdir(parents=True)
        minimax_dir.mkdir(parents=True)

        (gemini_dir / "gemini.json").write_text(
            json.dumps(
                {
                    "model": "gemini-3.7-flash",
                    "source_start_ms": 0,
                    "source_end_ms": 60000,
                    "attempt_count": 1,
                    "latency_ms": 1000,
                    "usage_metadata": {
                        "prompt_token_count": 1000,
                        "candidates_token_count": 200,
                    },
                }
            ),
            encoding="utf-8",
        )
        (minimax_dir / "minimax.json").write_text(
            json.dumps(
                {
                    "provider": "minimax",
                    "model": "MiniMax-M3",
                    "attempt_count": 2,
                    "latency_ms": 2500,
                    "prompt_version": "fixed-segments-v2-minimax-m3",
                    "usage_metadata": {
                        "input_tokens": 500,
                        "output_tokens": 150,
                        "billing_mode": "token_plan",
                    },
                }
            ),
            encoding="utf-8",
        )
        (job_dir / "correction-routing.json").write_text(
            json.dumps(
                {
                    "requested_policy": "M3_FIRST",
                    "initial_provider": "minimax-m3",
                    "initial_route_reason": "m3_available",
                    "m3_quota_state_at_start": "available",
                    "provider_switches": [
                        {
                            "from": "minimax-m3",
                            "to": "gemini-3.7-flash",
                            "reason": "invalid_response",
                            "at_segment_id": "seg-0001",
                        }
                    ],
                    "segment_counts": {
                        "minimax-m3": 1,
                        "gemini-3.7-flash": 9,
                        "chirp-3-raw": 0,
                    },
                }
            ),
            encoding="utf-8",
        )

        summary = build_performance_summary(
            self.database_path,
            self.data_dir,
            job["id"],
        )

        self.assertEqual(summary["accounting"]["geminiRetryCount"], 0)
        self.assertEqual(summary["accounting"]["minimaxRetryCount"], 1)
        self.assertEqual(summary["providerCallBreakdown"]["googleVertexAi"]["callCount"], 1)
        self.assertEqual(summary["providerCallBreakdown"]["minimax"]["callCount"], 1)
        self.assertEqual(summary["providerCallBreakdown"]["minimax"]["retryCount"], 1)
        self.assertEqual(summary["correctionRouting"]["requestedPolicy"], "M3_FIRST")
        self.assertEqual(
            summary["correctionRouting"]["providerSwitches"][0]["reason"],
            "invalid_response",
        )
        self.assertEqual(summary["providerCostBreakdown"]["minimaxBillingMode"], "token_plan")

    def test_observability_is_backward_compatible_and_secret_free(self) -> None:
        job = self._approved_job(duration_seconds=600)
        job_dir = self.data_dir / "jobs" / job["id"]
        (job_dir / "correction-m3-v1").mkdir(parents=True)
        (job_dir / "correction-m3-v1" / "m3-invalid.json").write_text(
            json.dumps({
                "provider": "minimax",
                "model": "MiniMax-M3",
                "attempt_count": 1,
                "response_valid": False,
                "error_type": "MiniMaxProviderError",
                "usage_metadata": {"input_tokens": 10, "output_tokens": 4096},
            }),
            encoding="utf-8",
        )
        (job_dir / "correction-routing.json").write_text(
            json.dumps({
                "requested_policy": "M3_FIRST",
                "initial_provider": "minimax-m3",
                "provider_switches": [{"to": "gemini-3.7-flash", "reason": "invalid_response", "at_segment_id": "seg-2"}],
                "effective_gemini_concurrency": 2,
                "effective_m3_concurrency": 1,
                "runtime_git_sha": "a" * 40,
                "docker_image_revision": "b" * 40,
                "m3_max_output_tokens": 4096,
            }),
            encoding="utf-8",
        )
        old_sha = os.environ.get("COURSE_TRANSCRIPT_RUNTIME_GIT_SHA")
        old_image = os.environ.get("COURSE_TRANSCRIPT_DOCKER_IMAGE_REVISION")
        old_limit = os.environ.get("MINIMAX_M3_MAX_OUTPUT_TOKENS")
        os.environ["COURSE_TRANSCRIPT_RUNTIME_GIT_SHA"] = "c" * 40
        os.environ["COURSE_TRANSCRIPT_DOCKER_IMAGE_REVISION"] = "d" * 40
        os.environ["MINIMAX_M3_MAX_OUTPUT_TOKENS"] = "8192"
        try:
            summary = build_performance_summary(self.database_path, self.data_dir, job["id"])
        finally:
            for key, old in (
                ("COURSE_TRANSCRIPT_RUNTIME_GIT_SHA", old_sha),
                ("COURSE_TRANSCRIPT_DOCKER_IMAGE_REVISION", old_image),
                ("MINIMAX_M3_MAX_OUTPUT_TOKENS", old_limit),
            ):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
        observed = summary["observability"]
        self.assertEqual(observed["runtimeGitSha"], "a" * 40)
        self.assertEqual(observed["dockerImageRevision"], "b" * 40)
        self.assertEqual(observed["finalProvider"], "gemini-3.7-flash")
        self.assertEqual(observed["providerSwitchReason"], "invalid_response")
        self.assertEqual(observed["providerSwitchAtSegment"], "seg-2")
        self.assertEqual(observed["effectiveGeminiConcurrency"], 2)
        self.assertEqual(observed["effectiveM3Concurrency"], 1)
        self.assertEqual(observed["minimaxInvalidResponseCount"], 1)
        self.assertEqual(observed["m3OutputTokenLimit"], 4096)
        self.assertTrue(observed["m3OutputLimitEvidenceAvailable"])
        self.assertEqual(observed["minimaxOutputLimitHitCount"], 1)
        self.assertNotIn("api_key", json.dumps(summary, ensure_ascii=False).lower())


if __name__ == "__main__":
    unittest.main()
