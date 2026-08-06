from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.jobs import delivery_worker
from app.jobs.completion import finish_with_policy
from app.jobs.delivery_state import record_delivery_success
from app.jobs.store import JobStore
from app.pipeline import dynamic_worker_production
from app.subtitles.review_publish import PublishReviewedRequest


def _seed_job(
    database: Path,
    *,
    job_id: str,
    require_human_review: bool,
    status: str,
    locked_by: str | None = None,
) -> JobStore:
    store = JobStore(database)
    now = datetime.now(UTC)
    batch_preview_id = f"preview-{job_id}"
    preview_id = f"source-{job_id}"
    batch_id = f"batch-{job_id}"
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO batch_previews(
                id, selection_mode, source_root, item_count, total_size_bytes,
                inspected_by, inspected_at, expires_at, consumed_at
            ) VALUES (?, 'files', NULL, 1, 1000, 'test', ?, ?, ?)
            """,
            (
                batch_preview_id,
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO source_previews(
                id, batch_preview_id, item_index, source_path, source_name,
                size_bytes, inspected_by, inspected_at, expires_at, consumed_at
            ) VALUES (?, ?, 0, ?, 'sample.mp4', 1000, 'test', ?, ?, ?)
            """,
            (
                preview_id,
                batch_preview_id,
                f"gdrive:tests/{job_id}/sample.mp4",
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO batches(
                id, batch_preview_id, name, selection_mode, source_root,
                status, item_count, created_by, created_at, updated_at
            ) VALUES (?, ?, 'test', 'files', NULL, ?, 1, 'test', ?, ?)
            """,
            (batch_id, batch_preview_id, status, now.isoformat(), now.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO jobs(
                id, preview_id, batch_id, queue_position, source_path,
                source_name, source_size_bytes, language_code, profile,
                enable_gemini_correction, enable_subtitles,
                require_human_review, status, active_stage, stage_detail,
                progress, reserved_cost_usd, actual_cost_usd, created_by,
                created_at, updated_at, revision, locked_by, lease_expires_at
            ) VALUES (?, ?, ?, 0, ?, 'sample.mp4', 1000, 'cmn-Hant-TW',
                'highest_accuracy', 1, 1, ?, ?, 'qa', 'ready', 99,
                '0.05', '0', 'test', ?, ?, 1, ?, ?)
            """,
            (
                job_id,
                preview_id,
                batch_id,
                f"gdrive:tests/{job_id}/sample.mp4",
                int(require_human_review),
                status,
                now.isoformat(),
                now.isoformat(),
                locked_by,
                (now + timedelta(minutes=5)).isoformat() if locked_by else None,
            ),
        )
    return store


class HumanReviewGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_review_required_stops_before_delivery(self) -> None:
        store = _seed_job(
            self.root / "jobs.db",
            job_id="review-job",
            require_human_review=True,
            status="quality_check",
            locked_by="worker-1",
        )

        result = finish_with_policy(
            store,
            job_id="review-job",
            worker_id="worker-1",
            drive_published=False,
        )

        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(result["active_stage"], "review")
        self.assertIsNone(result["locked_by"])
        self.assertEqual(
            store.get_batch("batch-review-job")["status"],
            "awaiting_review",
        )
        events = store.list_job_events("review-job")
        self.assertEqual(events[0]["event_type"], "local_outputs_ready_for_review")
        self.assertTrue(events[0]["payload"]["human_review_blocking"])

    def test_non_review_job_can_complete(self) -> None:
        store = _seed_job(
            self.root / "jobs.db",
            job_id="automatic-job",
            require_human_review=False,
            status="quality_check",
            locked_by="worker-1",
        )

        result = finish_with_policy(
            store,
            job_id="automatic-job",
            worker_id="worker-1",
            drive_published=True,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            store.get_batch("batch-automatic-job")["status"],
            "completed",
        )

    def test_production_auto_publish_gate_is_fail_closed(self) -> None:
        called: list[str] = []

        def publish(*args: object, **kwargs: object) -> dict[str, str]:
            called.append("published")
            return {"status": "completed"}

        @contextmanager
        def unlocked(*args: object, **kwargs: object):
            yield

        with (
            patch.object(dynamic_worker_production, "_ORIGINAL_AUTO_PUBLISH", publish),
            patch.object(dynamic_worker_production, "drive_publish_lock", unlocked),
        ):
            blocked = dynamic_worker_production._locked_auto_publish(
                object(),
                {"require_human_review": 1, "source_path": "gdrive:test/sample.mp4"},
                self.root,
                "worker",
            )
            self.assertIsNone(blocked)
            self.assertEqual(called, [])

            allowed = dynamic_worker_production._locked_auto_publish(
                object(),
                {"require_human_review": 0, "source_path": "gdrive:test/sample.mp4"},
                self.root,
                "worker",
            )
            self.assertEqual(allowed, {"status": "completed"})
            self.assertEqual(called, ["published"])

    def test_delivery_worker_does_not_select_review_blocked_job(self) -> None:
        data_dir = self.root / "data"
        database = data_dir / "course-transcript.db"
        _seed_job(
            database,
            job_id="blocked-delivery",
            require_human_review=True,
            status="awaiting_review",
        )
        job_dir = data_dir / "jobs" / "blocked-delivery"
        job_dir.mkdir(parents=True)
        (job_dir / "pipeline-manifest.json").write_text(
            json.dumps({"drive_publication_status": "pending_retry"}),
            encoding="utf-8",
        )
        (job_dir / "drive-publish-state.json").write_text(
            json.dumps({"status": "prepared"}),
            encoding="utf-8",
        )

        with (
            patch.object(delivery_worker, "DATA_DIR", data_dir),
            patch.object(delivery_worker, "DATABASE", database),
        ):
            self.assertIsNone(delivery_worker._candidate())

    def test_zero_edit_review_request_and_editor_publish_complete_job(self) -> None:
        payload = PublishReviewedRequest(
            expected_revision=0,
            output_formats=["srt", "txt"],
        )
        self.assertEqual(payload.expected_revision, 0)

        database = self.root / "jobs.db"
        store = _seed_job(
            database,
            job_id="reviewed-job",
            require_human_review=True,
            status="awaiting_review",
        )
        result = record_delivery_success(
            database,
            job_id="reviewed-job",
            actor="reviewer@example.com",
            source="editor",
            backup_count=0,
            published_revision=0,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["active_stage"], "completed")
        self.assertEqual(
            store.get_batch("batch-reviewed-job")["status"],
            "completed",
        )

    def test_background_delivery_cannot_release_review_gate(self) -> None:
        database = self.root / "jobs.db"
        _seed_job(
            database,
            job_id="blocked-background",
            require_human_review=True,
            status="awaiting_review",
        )

        with self.assertRaisesRegex(RuntimeError, "human-review-blocked"):
            record_delivery_success(
                database,
                job_id="blocked-background",
                actor="delivery-worker",
                source="delivery_worker",
                backup_count=0,
            )


if __name__ == "__main__":
    unittest.main()
