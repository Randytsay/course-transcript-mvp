from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.jobs.source import DriveEntry, SourceMetadata
from app.jobs.costs import CostConfig


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.access_env = patch.dict(
            os.environ,
            {
                "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "false",
                "COURSE_TRANSCRIPT_PUBLIC_ORIGIN": "",
            },
        )
        self.access_env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        job = self.data / "jobs" / "sample-job"
        job.mkdir(parents=True)
        (job / "source.mp3").write_bytes(b"not media")
        (job / "subtitles.json").write_text(json.dumps({"segments": [{"segment_id": "seg-1", "start_ms": 100, "end_ms": 900, "raw_text": "原文"}]}), encoding="utf-8")
        (job / "subtitles-corrected.json").write_text(json.dumps({"segments": [{"segment_id": "seg-1", "start_ms": 100, "end_ms": 900, "corrected_text": "校正後", "uncertain_terms": ["術語"]}]}), encoding="utf-8")
        (job / "qa-report.json").write_text(json.dumps({"status": "PASS", "audio": {"duration_ms": 2000}, "chirp": {"word_count": 2}}), encoding="utf-8")
        (job / "subtitles.ass").write_text("[Script Info]\n", encoding="utf-8")
        (job / "review-terms.json").write_text(
            json.dumps(
                [
                    {
                        "id": "a" * 16,
                        "heard": "都率天",
                        "suggestion": "兜率天",
                        "timestamp": "00:00:01",
                        "confidence": "low",
                        "status": "pending",
                        "scope": "session",
                    }
                ]
            ),
            encoding="utf-8",
        )
        import app.api as api
        api.DATA_DIR = self.data
        api.JOBS_DIR = self.data / "jobs"
        api._store_cache = None
        self.api = api
        self.client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(api.app)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.access_env.stop()

    def test_read_only_job_api(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        self.assertTrue(self.client.get("/api/v1/health").json()["database_available"])
        self.assertEqual(self.client.get("/api/v1/jobs").json()["jobs"][0]["id"], "sample-job")
        segment = self.client.get("/api/v1/jobs/sample-job/segments").json()["segments"][0]
        self.assertEqual(segment["corrected_text"], "校正後")
        self.assertEqual(segment["start_ms"], 100)
        job_data = self.client.get("/api/v1/jobs/sample-job").json()
        self.assertEqual(job_data["words"], 2)
        self.assertEqual(job_data["duration_seconds"], 2.0)
        artifact_ids = {item["id"] for item in self.client.get("/api/v1/jobs/sample-job/artifacts").json()["artifacts"]}
        self.assertIn("subtitles.ass", artifact_ids)
        opened = self.client.get("/api/v1/jobs/sample-job/artifacts/subtitles.ass")
        self.assertEqual(opened.status_code, 200)
        self.assertIn("inline", opened.headers["content-disposition"])
        self.assertEqual(self.client.get("/api/v1/jobs/sample-job/artifacts/../../source.mp3").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/jobs/../secrets").status_code, 404)

    def test_safe_preflight_job_and_cost_approval(self) -> None:
        metadata = SourceMetadata(
            source_path="gdrive:課程/測試.mp3",
            name="測試.mp3",
            size_bytes=123456,
            modified_at="2026-07-31T00:00:00Z",
            mime_type="audio/mpeg",
        )
        with patch.object(self.api, "inspect_rclone_source", return_value=metadata):
            inspected = self.client.post(
                "/api/v1/drive/inspect",
                json={"source_path": metadata.source_path},
            )
        self.assertEqual(inspected.status_code, 200)
        preview_id = inspected.json()["preview_id"]
        self.assertFalse(inspected.json()["paid_operation_started"])
        self.assertNotIn("source_path", inspected.json())

        created = self.client.post(
            "/api/v1/jobs",
            json={
                "preview_id": preview_id,
                "language_code": "cmn-Hant-TW",
                "profile": "highest_accuracy",
                "enable_gemini_correction": True,
                "enable_subtitles": True,
                "require_human_review": True,
                "output_formats": ["srt", "pdf"],
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "preflight")
        self.assertFalse(created.json()["paid_operation_started"])
        job_id = created.json()["job_id"]
        self.assertEqual(
            self.client.get(f"/api/v1/jobs/{job_id}").json()["output_formats"],
            ["srt", "pdf"],
        )

        store = self.api._store()
        store.acquire_lease(job_id, "test-worker")
        estimated = store.set_cost_estimate(
            job_id=job_id,
            duration_seconds=3600,
            estimated_cost_usd=Decimal("2.7500"),
            pricing_version="test-pricing",
            worker_id="test-worker",
        )
        approved = self.client.post(
            f"/api/v1/jobs/{job_id}/approve",
            json={
                "expected_revision": estimated["revision"],
                "confirmed_estimated_cost_usd": "2.7500",
            },
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "queued")
        self.assertEqual(approved.json()["reserved_cost_usd"], "2.7500")
        costs = self.client.get("/api/v1/costs").json()
        self.assertEqual(costs["project_limit_usd"], "200")
        self.assertEqual(costs["committed_estimated_cost_usd"], "2.7500")

    def test_mutations_require_cloudflare_identity_when_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "true",
                "COURSE_TRANSCRIPT_PUBLIC_ORIGIN": "https://transcript.example.test",
            },
        ):
            blocked = self.client.post(
                "/api/v1/drive/inspect",
                json={"source_path": "gdrive:課程/測試.mp3"},
            )
            self.assertEqual(blocked.status_code, 401)
            wrong_origin = self.client.post(
                "/api/v1/drive/inspect",
                json={"source_path": "gdrive:課程/測試.mp3"},
                headers={
                    "Cf-Access-Authenticated-User-Email": "user@example.test",
                    "Cf-Access-Jwt-Assertion": "test-assertion",
                    "Origin": "https://evil.example",
                },
            )
            self.assertEqual(wrong_origin.status_code, 403)

    def test_twd_budget_starts_from_operator_balance_and_deducts_new_estimates(self) -> None:
        with patch.dict(
            os.environ,
            {
                "COURSE_TRANSCRIPT_BUDGET_REMAINING_TWD": "1200",
                "COURSE_TRANSCRIPT_USD_TO_TWD": "32",
                "COURSE_TRANSCRIPT_BUDGET_BASELINE_COMMITTED_USD": "132.6374",
            },
        ):
            config = CostConfig.from_env()
            initial = config.budget_summary(Decimal("132.6374"))
            after_new_estimate = config.budget_summary(Decimal("132.8874"))
            api_costs = self.client.get("/api/v1/costs").json()

        self.assertEqual(config.project_limit_usd, Decimal("170.1374"))
        self.assertEqual(initial["budget_currency"], "TWD")
        self.assertEqual(initial["remaining_estimated_budget_twd"], "1200.00")
        self.assertEqual(after_new_estimate["committed_estimated_cost_twd"], "8.00")
        self.assertEqual(after_new_estimate["remaining_estimated_budget_twd"], "1192.00")
        self.assertEqual(api_costs["budget_currency"], "TWD")
        self.assertEqual(api_costs["remaining_estimated_budget_twd"], "1200.00")
        self.assertEqual(api_costs["fx_source"], "configured_manual")
        self.assertFalse(api_costs["fx_auto_enabled"])

    def test_term_decision_preserves_original_transcript(self) -> None:
        original = (self.data / "jobs" / "sample-job" / "subtitles.json").read_bytes()
        decided = self.client.patch(
            f"/api/v1/jobs/sample-job/review-terms/{'a' * 16}",
            json={
                "action": "confirmed",
                "approved_value": "兜率天",
                "scope": "session",
            },
        )
        self.assertEqual(decided.status_code, 200)
        self.assertFalse(decided.json()["original_transcript_modified"])
        self.assertEqual(decided.json()["term"]["status"], "confirmed")
        self.assertEqual(
            (self.data / "jobs" / "sample-job" / "subtitles.json").read_bytes(),
            original,
        )

    def test_browse_preview_and_create_multi_file_batch(self) -> None:
        entries = [
            DriveEntry(
                source_path="gdrive:課程/第一堂.mp3",
                name="第一堂.mp3",
                is_dir=False,
                size_bytes=100,
                modified_at="2026-07-31T00:00:00Z",
                mime_type="audio/mpeg",
                supported_media=True,
            ),
            DriveEntry(
                source_path="gdrive:課程/講義",
                name="講義",
                is_dir=True,
                size_bytes=0,
                modified_at=None,
                mime_type=None,
                supported_media=False,
            ),
        ]
        with patch.object(
            self.api, "list_rclone_directory", return_value=("gdrive:課程", entries)
        ):
            browsed = self.client.post(
                "/api/v1/drive/browse", json={"source_path": "gdrive:課程"}
            )
        self.assertEqual(browsed.status_code, 200)
        self.assertEqual(len(browsed.json()["entries"]), 2)
        self.assertFalse(browsed.json()["paid_operation_started"])

        metadata = [
            SourceMetadata(
                source_path="gdrive:課程/第一堂.mp3",
                name="第一堂.mp3",
                size_bytes=100,
                modified_at=None,
                mime_type="audio/mpeg",
            ),
            SourceMetadata(
                source_path="gdrive:課程/第二堂.m4a",
                name="第二堂.m4a",
                size_bytes=200,
                modified_at=None,
                mime_type="audio/mp4",
            ),
        ]
        with patch.object(
            self.api, "inspect_rclone_selection", return_value=metadata
        ):
            previewed = self.client.post(
                "/api/v1/drive/preview-batch",
                json={
                    "selection_mode": "files",
                    "source_paths": [item.source_path for item in metadata],
                },
            )
        self.assertEqual(previewed.status_code, 200)
        self.assertEqual(previewed.json()["item_count"], 2)
        self.assertEqual(previewed.json()["total_size_bytes"], 300)
        self.assertFalse(previewed.json()["paid_operation_started"])

        created = self.client.post(
            "/api/v1/batches",
            json={
                "batch_preview_id": previewed.json()["batch_preview_id"],
                "language_code": "cmn-Hant-TW",
                "profile": "highest_accuracy",
                "enable_gemini_correction": True,
                "enable_subtitles": True,
                "require_human_review": True,
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["item_count"], 2)
        self.assertEqual(len(created.json()["job_ids"]), 2)
        batch = self.client.get(
            f"/api/v1/batches/{created.json()['batch_id']}"
        )
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(len(batch.json()["jobs"]), 2)
        self.assertEqual(batch.json()["jobs"][0]["output_formats"], ["srt", "txt", "csv"])
        store = self.api._store()
        for job_id in created.json()["job_ids"]:
            store.acquire_lease(job_id, "test-worker")
            store.record_preflight_result(
                job_id=job_id,
                duration_seconds=300,
                source_checksum="a" * 64,
                media_format="mp3",
                audio_codec="mp3",
                estimated_cost_usd=Decimal("0.50"),
                pricing_version="test",
                worker_id="test-worker",
            )
        ready = self.client.get(
            f"/api/v1/batches/{created.json()['batch_id']}"
        ).json()
        self.assertEqual(ready["status"], "awaiting_confirmation")
        approved = self.client.post(
            f"/api/v1/batches/{created.json()['batch_id']}/approve",
            json={
                "expected_revision": ready["revision"],
                "confirmed_estimated_cost_usd": ready["estimated_cost_usd"],
            },
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "queued")
        self.assertTrue(approved.json()["paid_operation_authorized"])
        self.assertFalse(approved.json()["paid_operation_started"])


if __name__ == "__main__":
    unittest.main()
