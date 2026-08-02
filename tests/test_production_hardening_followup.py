from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ProductionHardeningFollowupTests(unittest.TestCase):
    def test_standard_recovery_treats_transient_error_as_pending(self) -> None:
        from app.providers import run_chirp_pipeline_hardened as hardened

        counts = {
            "done": 1,
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
            "standard-job",
        ), patch.object(
            hardened.base,
            "DYNAMIC_BATCHING",
            False,
        ), patch.object(
            hardened.base,
            "RECOVER_ONCE",
            False,
        ), patch.object(
            hardened.base,
            "_parallel_phase",
            return_value=counts,
        ), patch.object(
            hardened.base,
            "_merge",
            side_effect=AssertionError("merge must not run while retryable"),
        ):
            self.assertEqual(hardened._recover_pass([(0, 0.0, 10.0)]), 75)

    def test_gemini_audit_is_versioned_and_legacy_record_is_preserved(self) -> None:
        from app.providers import correct_text_hardened as hardened

        item = {
            "segment_id": "a",
            "start_ms": 0,
            "end_ms": 1000,
            "raw_text": "彌勒菩薩",
        }
        response = SimpleNamespace(
            text=json.dumps(
                {
                    "segments": [
                        {
                            "segment_id": "a",
                            "corrected_text": "彌勒菩薩",
                            "uncertain_terms": [],
                        }
                    ]
                }
            ),
            usage_metadata=None,
        )
        metrics = {
            "request_started_at": "2026-08-02T00:00:00+00:00",
            "response_completed_at": "2026-08-02T00:00:01+00:00",
            "latency_ms": 1000,
            "attempt_count": 1,
            "retry_events": [],
        }
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            legacy = work / "a.json"
            legacy.write_text('{"legacy":true}\n', encoding="utf-8")
            with patch.object(hardened.base, "WORK", work), patch.object(
                hardened,
                "generate_json",
                return_value=(response, metrics),
            ) as generated:
                first = hardened.correct_window([item], [])
                second = hardened.correct_window([item], [])
            self.assertEqual(first, second)
            self.assertEqual(generated.call_count, 1)
            self.assertEqual(legacy.read_text(encoding="utf-8"), '{"legacy":true}\n')
            records = list(
                work.glob(
                    "a.fixed-segments-v4-production-hardening.*.json"
                )
            )
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(
                record["prompt_version"],
                hardened.PROMPT_VERSION,
            )
            self.assertTrue(record["raw_response"])

    def test_delivery_worker_respects_all_editor_owned_states(self) -> None:
        from app.jobs.delivery_worker import _superseded

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            path = job / "drive-delivery-state.json"
            for status in (
                "editor_publish_in_progress",
                "editor_publish_failed",
                "superseded_by_editor",
            ):
                path.write_text(
                    json.dumps({"status": status, "editor_revision": 3}),
                    encoding="utf-8",
                )
                self.assertTrue(_superseded(job), status)
            path.write_text(
                json.dumps({"status": "pending_retry"}),
                encoding="utf-8",
            )
            self.assertFalse(_superseded(job))

    def test_delivery_candidate_includes_retained_awaiting_review_job(self) -> None:
        from app.jobs import delivery_worker

        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            jobs_dir = data_dir / "jobs" / "legacy-job"
            jobs_dir.mkdir(parents=True)
            (jobs_dir / "pipeline-manifest.json").write_text(
                json.dumps({"drive_publication_status": "pending_retry"}),
                encoding="utf-8",
            )
            database = data_dir / "course-transcript.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE jobs(
                        id TEXT,
                        status TEXT,
                        source_path TEXT,
                        source_name TEXT,
                        output_formats_json TEXT,
                        updated_at TEXT,
                        created_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO jobs VALUES(
                        'legacy-job', 'awaiting_review',
                        'gdrive:course/lesson.mp3', 'lesson.mp3',
                        '["srt","txt"]',
                        '2026-08-01T00:00:00+00:00',
                        '2026-08-01T00:00:00+00:00'
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with patch.object(delivery_worker, "DATA_DIR", data_dir), patch.object(
                delivery_worker,
                "DATABASE",
                database,
            ):
                selected = delivery_worker._candidate()
            self.assertIsNotNone(selected)
            self.assertEqual(selected["id"], "legacy-job")

    def test_editor_intent_marker_precedes_remote_ownership(self) -> None:
        from app.subtitles.editor_hardened import (
            _mark_editor_intent,
            _marker_revision,
        )

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _mark_editor_intent(
                directory,
                revision=7,
                actor="tester",
            )
            state = json.loads(
                (directory / "drive-delivery-state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["status"], "editor_publish_in_progress")
            self.assertEqual(_marker_revision(state), 7)


if __name__ == "__main__":
    unittest.main()
