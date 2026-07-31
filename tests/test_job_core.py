from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from app.jobs.costs import CostConfig, estimate_job_cost
from app.jobs.source import (
    DriveEntry,
    SourceInspectionError,
    SourceMetadata,
    inspect_rclone_selection,
    list_rclone_directory,
    validate_directory_path,
    validate_source_path,
)
from app.jobs.store import JobConflict, JobStore


class CostTests(unittest.TestCase):
    def test_estimate_is_auditable_and_positive(self) -> None:
        estimate = estimate_job_cost(3600, CostConfig())
        self.assertEqual(estimate.pricing_version, "google-cloud-public-pricing-2026-07-31")
        self.assertGreater(estimate.chirp_billable_minutes, Decimal("60"))
        self.assertGreater(estimate.estimated_total_usd, estimate.subtotal_usd)
        self.assertEqual(estimate.to_dict()["estimated_total_usd"], str(estimate.estimated_total_usd))


class SourceValidationTests(unittest.TestCase):
    def test_allows_single_media_beneath_prefix(self) -> None:
        self.assertEqual(
            validate_source_path("gdrive:課程/第一堂.mp3", "gdrive:課程/"),
            "gdrive:課程/第一堂.mp3",
        )

    def test_rejects_traversal_folder_and_wrong_prefix(self) -> None:
        for candidate in (
            "gdrive:課程/../私密/第一堂.mp3",
            "gdrive:課程/資料夾/",
            "gdrive:其他/第一堂.mp3",
            "gdrive:課程/說明.txt",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(SourceInspectionError):
                    validate_source_path(candidate, "gdrive:課程/")

    def test_allows_drive_root_and_nested_directory(self) -> None:
        self.assertEqual(validate_directory_path("gdrive:", "gdrive:"), "gdrive:")
        self.assertEqual(
            validate_directory_path("gdrive:課程/第一期", "gdrive:"),
            "gdrive:課程/第一期",
        )

    @patch("app.jobs.source._run_lsjson")
    def test_folder_selection_filters_media_and_preserves_relative_paths(
        self, run_lsjson: Mock
    ) -> None:
        run_lsjson.return_value = [
            {"Path": "第一堂.mp3", "Name": "第一堂.mp3", "Size": 100},
            {"Path": "子資料夾/第二堂.m4a", "Name": "第二堂.m4a", "Size": 200},
            {"Path": "講義.pdf", "Name": "講義.pdf", "Size": 300},
        ]
        items = inspect_rclone_selection(
            selection_mode="folder", source_paths=["gdrive:課程"]
        )
        self.assertEqual(
            {item.source_path for item in items},
            {"gdrive:課程/第一堂.mp3", "gdrive:課程/子資料夾/第二堂.m4a"},
        )

    @patch("app.jobs.source._run_lsjson")
    def test_directory_browser_marks_supported_media(self, run_lsjson: Mock) -> None:
        run_lsjson.return_value = [
            {"Path": "子資料夾", "Name": "子資料夾", "IsDir": True},
            {"Path": "第一堂.mp3", "Name": "第一堂.mp3", "Size": 100},
            {"Path": "講義.pdf", "Name": "講義.pdf", "Size": 100},
        ]
        current, entries = list_rclone_directory("gdrive:")
        self.assertEqual(current, "gdrive:")
        self.assertIsInstance(entries[0], DriveEntry)
        self.assertEqual(
            [entry.supported_media for entry in entries], [False, True, False]
        )


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_job(self) -> dict:
        preview = self.store.create_preview(
            source_path="gdrive:課程/第一堂.mp3",
            source_name="第一堂.mp3",
            size_bytes=100,
            modified_at=None,
            mime_type="audio/mpeg",
            actor="owner@example.test",
        )
        return self.store.create_preflight_job(
            preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="owner@example.test",
        )

    def test_single_active_job_and_lease_ownership(self) -> None:
        job = self._create_job()
        with self.assertRaises(JobConflict):
            self._create_job()
        leased = self.store.acquire_lease(job["id"], "worker-a")
        self.assertEqual(leased["locked_by"], "worker-a")
        with self.assertRaises(JobConflict):
            self.store.acquire_lease(job["id"], "worker-b")
        heartbeat = self.store.heartbeat(job["id"], "worker-a")
        self.assertIsNotNone(heartbeat["last_heartbeat_at"])
        self.store.release_lease(job["id"], "worker-a")
        self.assertIsNone(self.store.get_job(job["id"])["locked_by"])

    def test_cost_cap_rejects_over_limit(self) -> None:
        job = self._create_job()
        self.store.acquire_lease(job["id"], "worker-a")
        estimated = self.store.set_cost_estimate(
            job_id=job["id"],
            duration_seconds=3600,
            estimated_cost_usd=Decimal("201"),
            pricing_version="test",
            worker_id="worker-a",
        )
        with self.assertRaises(JobConflict):
            self.store.approve_job(
                job_id=job["id"],
                expected_revision=estimated["revision"],
                confirmed_estimated_cost_usd=Decimal("201"),
                project_limit_usd=Decimal("200"),
                actor="owner@example.test",
            )

    def test_batch_creates_ordered_preflight_jobs(self) -> None:
        preview = self.store.create_batch_preview(
            selection_mode="files",
            source_root=None,
            items=[
                SourceMetadata(
                    source_path="gdrive:課程/第一堂.mp3",
                    name="第一堂.mp3",
                    size_bytes=100,
                    modified_at=None,
                    mime_type=None,
                ).to_dict(),
                SourceMetadata(
                    source_path="gdrive:課程/第二堂.m4a",
                    name="第二堂.m4a",
                    size_bytes=200,
                    modified_at=None,
                    mime_type=None,
                ).to_dict(),
            ],
            actor="owner@example.test",
        )
        result = self.store.create_preflight_batch(
            batch_preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="owner@example.test",
        )
        self.assertEqual(result["batch"]["item_count"], 2)
        self.assertEqual(
            [job["queue_position"] for job in result["jobs"]], [0, 1]
        )
        first, second = result["jobs"]
        self.store.acquire_lease(first["id"], "worker-a")
        with self.assertRaises(JobConflict):
            self.store.acquire_lease(second["id"], "worker-b")

    def test_batch_waits_for_all_preflights_then_reserves_total(self) -> None:
        preview = self.store.create_batch_preview(
            selection_mode="folder",
            source_root="gdrive:課程",
            items=[
                {
                    "source_path": f"gdrive:課程/第{index}堂.mp3",
                    "name": f"第{index}堂.mp3",
                    "size_bytes": 100 * index,
                    "modified_at": None,
                    "mime_type": "audio/mpeg",
                }
                for index in (1, 2)
            ],
            actor="owner@example.test",
        )
        result = self.store.create_preflight_batch(
            batch_preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="owner@example.test",
        )
        for index, job in enumerate(result["jobs"], start=1):
            self.store.acquire_lease(job["id"], "worker-a")
            self.store.record_preflight_result(
                job_id=job["id"],
                duration_seconds=600 * index,
                source_checksum=f"{index:064x}",
                media_format="mp3",
                audio_codec="mp3",
                estimated_cost_usd=Decimal(str(index)),
                pricing_version="test",
                worker_id="worker-a",
            )
        batch = self.store.get_batch(result["batch"]["id"])
        self.assertEqual(batch["status"], "awaiting_confirmation")
        self.assertEqual(batch["estimated_cost_usd"], "3")
        approved = self.store.approve_batch(
            batch_id=batch["id"],
            expected_revision=batch["revision"],
            confirmed_estimated_cost_usd=Decimal("3"),
            project_limit_usd=Decimal("200"),
            actor="owner@example.test",
        )
        self.assertEqual(approved["status"], "queued")
        self.assertEqual(approved["reserved_cost_usd"], "3")
        self.assertEqual(
            [job["reserved_cost_usd"] for job in approved["jobs"]], ["1", "2"]
        )


if __name__ == "__main__":
    unittest.main()
