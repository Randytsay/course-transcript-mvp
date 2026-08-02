from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProductionHardeningExtraTests(unittest.TestCase):
    def test_retryable_chunk_propagates_exit_76_for_backoff(self) -> None:
        from app.providers import run_chirp_pipeline_hardened as hardened

        counts = {
            "done": 2,
            "submitted": 0,
            "pending": 0,
            "retryable": 1,
            "failed": 0,
        }
        with tempfile.TemporaryDirectory() as temp, patch.object(
            hardened.base,
            "JOB",
            Path(temp),
        ), patch.object(
            hardened.base,
            "JOB_NAME",
            "job-test",
        ), patch.object(
            hardened.base,
            "DYNAMIC_BATCHING",
            True,
        ), patch.object(
            hardened.base,
            "RECOVER_ONCE",
            True,
        ), patch.object(
            hardened.base,
            "_parallel_phase",
            return_value=counts,
        ), patch.object(
            hardened.base,
            "_merge",
            side_effect=AssertionError("merge must not run while retryable"),
        ):
            self.assertEqual(hardened._recover_pass([(0, 0.0, 10.0)]), 76)

    def test_global_drive_lock_persists_release_timestamp(self) -> None:
        from app.jobs.drive_lock import drive_publish_lock

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"DRIVE_GLOBAL_MIN_INTERVAL_SECONDS": "0"},
        ):
            data_dir = Path(temp)
            with drive_publish_lock(data_dir, "gdrive:course/lesson.mp3"):
                pass
            path = data_dir / "locks" / "drive-publish-global.lock"
            self.assertTrue(path.is_file())
            self.assertGreater(float(path.read_text(encoding="utf-8")), 0)

    def test_delivery_success_updates_job_and_deduplicates_events(self) -> None:
        from app.jobs.delivery_state import record_delivery_success
        from app.jobs.store import JobStore

        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "course-transcript.db"
            store = JobStore(database)
            now = "2026-08-02T00:00:00+00:00"
            with store.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO source_previews(
                        id, batch_preview_id, item_index, source_path,
                        source_name, size_bytes, modified_at, mime_type,
                        inspected_by, inspected_at, expires_at, consumed_at
                    ) VALUES (?, NULL, 0, ?, ?, 1, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        "preview-1",
                        "gdrive:course/lesson.mp3",
                        "lesson.mp3",
                        "audio/mpeg",
                        "tester",
                        now,
                        "2026-08-03T00:00:00+00:00",
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, preview_id, batch_id, queue_position, source_path,
                        source_name, source_size_bytes, language_code, profile,
                        enable_gemini_correction, enable_subtitles,
                        require_human_review, chirp_max_parallel_chunks,
                        output_formats_json, status, active_stage, stage_detail,
                        progress, error, duration_seconds, source_checksum,
                        media_format, audio_codec, estimated_cost_usd,
                        reserved_cost_usd, actual_cost_usd, pricing_version,
                        approved_by, approved_at, created_by, created_at,
                        updated_at, revision, locked_by, lease_expires_at,
                        last_heartbeat_at
                    ) VALUES (
                        ?, ?, NULL, 0, ?, ?, 1, ?, ?, 1, 1, 0, 3,
                        '["srt","txt"]', 'completed', 'completed', ?, 100,
                        NULL, 60, 'sha', 'mp3', 'mp3', '1', '1', '0',
                        'test', 'tester', ?, 'tester', ?, ?, 1, NULL, NULL, NULL
                    )
                    """,
                    (
                        "job-1",
                        "preview-1",
                        "gdrive:course/lesson.mp3",
                        "lesson.mp3",
                        "cmn-Hant-TW",
                        "highest_accuracy",
                        "Drive 回寫待重試",
                        now,
                        now,
                        now,
                    ),
                )

            first = record_delivery_success(
                database,
                job_id="job-1",
                actor="delivery-worker",
                source="delivery_worker",
                backup_count=1,
            )
            second = record_delivery_success(
                database,
                job_id="job-1",
                actor="delivery-worker",
                source="delivery_worker",
                backup_count=1,
            )
            self.assertIn("重試成功", first["stage_detail"])
            self.assertEqual(first["revision"], second["revision"])

            editor_one = record_delivery_success(
                database,
                job_id="job-1",
                actor="editor-user",
                source="editor",
                backup_count=2,
                published_revision=1,
            )
            editor_two = record_delivery_success(
                database,
                job_id="job-1",
                actor="editor-user",
                source="editor",
                backup_count=2,
                published_revision=2,
            )
            editor_two_repeat = record_delivery_success(
                database,
                job_id="job-1",
                actor="editor-user",
                source="editor",
                backup_count=2,
                published_revision=2,
            )
            self.assertGreater(editor_two["revision"], editor_one["revision"])
            self.assertEqual(
                editor_two["revision"],
                editor_two_repeat["revision"],
            )

            connection = sqlite3.connect(database)
            try:
                delivery_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM job_events
                    WHERE job_id='job-1'
                      AND event_type='job_drive_delivery_completed'
                    """
                ).fetchone()[0]
                editor_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM job_events
                    WHERE job_id='job-1'
                      AND event_type='job_drive_editor_published'
                    """
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(delivery_count, 1)
            self.assertEqual(editor_count, 2)


if __name__ == "__main__":
    unittest.main()
