from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.jobs.costs import CostConfig
from app.jobs.preflight import _auto_authorize_after_preflight
from app.jobs.store import JobStore


class AutoAuthorizePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "course-transcript.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _single_job(self) -> dict[str, object]:
        preview = self.store.create_preview(
            source_path="gdrive:課程/測試.mp3",
            source_name="測試.mp3",
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

    def _record(self, job: dict[str, object], amount: str) -> dict[str, object]:
        self.store.acquire_lease(str(job["id"]), "preflight-worker")
        return self.store.record_preflight_result(
            job_id=str(job["id"]),
            duration_seconds=600,
            source_checksum="a" * 64,
            media_format="mp3",
            audio_codec="mp3",
            estimated_cost_usd=Decimal(amount),
            pricing_version="test",
            worker_id="preflight-worker",
        )

    def test_single_job_auto_queues_inside_guardrails(self) -> None:
        recorded = self._record(self._single_job(), "2.50")
        queued = _auto_authorize_after_preflight(
            self.store,
            recorded,
            config=CostConfig(
                project_limit_usd=Decimal("200"),
                auto_authorize_costs=True,
                auto_authorize_max_usd=Decimal("10"),
            ),
            actor="preflight-worker:auto",
        )
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["reserved_cost_usd"], "2.50")
        self.assertIsNotNone(queued["approved_at"])

    def test_single_job_above_auto_threshold_waits_for_operator(self) -> None:
        recorded = self._record(self._single_job(), "12.00")
        waiting = _auto_authorize_after_preflight(
            self.store,
            recorded,
            config=CostConfig(
                project_limit_usd=Decimal("200"),
                auto_authorize_costs=True,
                auto_authorize_max_usd=Decimal("10"),
            ),
            actor="preflight-worker:auto",
        )
        self.assertEqual(waiting["status"], "awaiting_confirmation")
        self.assertEqual(waiting["reserved_cost_usd"], "0")

    def test_project_limit_still_blocks_auto_authorization(self) -> None:
        recorded = self._record(self._single_job(), "2.50")
        waiting = _auto_authorize_after_preflight(
            self.store,
            recorded,
            config=CostConfig(
                project_limit_usd=Decimal("2"),
                auto_authorize_costs=True,
                auto_authorize_max_usd=Decimal("10"),
            ),
            actor="preflight-worker:auto",
        )
        self.assertEqual(waiting["status"], "awaiting_confirmation")
        self.assertIsNone(waiting["approved_at"])

    def test_batch_auto_queues_only_after_all_preflights_finish(self) -> None:
        preview = self.store.create_batch_preview(
            selection_mode="files",
            source_root=None,
            items=[
                {
                    "source_path": "gdrive:課程/第一堂.mp3",
                    "name": "第一堂.mp3",
                    "size_bytes": 100,
                    "modified_at": None,
                    "mime_type": "audio/mpeg",
                },
                {
                    "source_path": "gdrive:課程/第二堂.mp3",
                    "name": "第二堂.mp3",
                    "size_bytes": 200,
                    "modified_at": None,
                    "mime_type": "audio/mpeg",
                },
            ],
            actor="owner@example.test",
        )
        created = self.store.create_preflight_batch(
            batch_preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="owner@example.test",
        )
        config = CostConfig(
            project_limit_usd=Decimal("200"),
            auto_authorize_costs=True,
            auto_authorize_max_usd=Decimal("10"),
        )

        first = self._record(created["jobs"][0], "1.25")
        first_after = _auto_authorize_after_preflight(
            self.store, first, config=config, actor="preflight-worker:auto"
        )
        self.assertEqual(first_after["status"], "awaiting_confirmation")
        self.assertEqual(self.store.get_batch(created["batch"]["id"])["status"], "preflight")

        second = self._record(created["jobs"][1], "2.25")
        second_after = _auto_authorize_after_preflight(
            self.store, second, config=config, actor="preflight-worker:auto"
        )
        batch = self.store.get_batch(created["batch"]["id"])
        self.assertEqual(second_after["status"], "queued")
        self.assertEqual(batch["status"], "queued")
        self.assertEqual(batch["reserved_cost_usd"], "3.50")
        self.assertEqual(
            [job["reserved_cost_usd"] for job in batch["jobs"]], ["1.25", "2.25"]
        )


if __name__ == "__main__":
    unittest.main()
