from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.subtitles import editor as base
from app.subtitles.publish_status import get_publish_status


class PublishStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.jobs_dir = self.data_dir / "jobs"
        self.imported_dir = self.data_dir / "imported-subtitles"
        self.jobs_dir.mkdir()
        self.imported_dir.mkdir()

        self.original = (base.DATA_DIR, base.JOBS_DIR, base.IMPORTED_DIR)
        base.DATA_DIR = self.data_dir
        base.JOBS_DIR = self.jobs_dir
        base.IMPORTED_DIR = self.imported_dir

        self.database = self.data_dir / "course-transcript.db"
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE batches (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL
                );
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def tearDown(self) -> None:
        base.DATA_DIR, base.JOBS_DIR, base.IMPORTED_DIR = self.original
        self.temporary.cleanup()

    def create_job(
        self,
        job_id: str = "job-1",
        *,
        job_status: str = "awaiting_review",
        batch_status: str = "awaiting_review",
        revision: int = 0,
        history: list[dict[str, object]] | None = None,
    ) -> Path:
        directory = self.jobs_dir / job_id
        directory.mkdir()
        (directory / "subtitles.json").write_text(
            json.dumps(
                {
                    "segments": [
                        {
                            "segment_id": "1",
                            "start_ms": 0,
                            "end_ms": 1000,
                            "text": "test",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        if revision != 0 or history is not None:
            self.write_json(
                directory / "subtitle-editor.json",
                {
                    "revision": revision,
                    "edits": {},
                    "history": history or [],
                },
            )

        batch_id = f"batch-{job_id}"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO batches(id, status) VALUES (?, ?)",
                (batch_id, batch_status),
            )
            connection.execute(
                """
                INSERT INTO jobs(id, batch_id, source_path, status)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, batch_id, f"gdrive:folder/{job_id}.mp4", job_status),
            )
        return directory

    @staticmethod
    def write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def add_event(self, job_id: str, revision: int) -> None:
        payload = {
            "source": "editor",
            "backup_count": 2,
            "published_revision": revision,
            "paid_provider_repeated": False,
            "human_review_released": True,
        }
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT INTO job_events(job_id, event_type, payload_json)
                VALUES (?, 'job_drive_editor_published', ?)
                """,
                (job_id, json.dumps(payload)),
            )

    def write_marker(self, directory: Path, status: str, revision: int) -> None:
        self.write_json(
            directory / "drive-delivery-state.json",
            {
                "status": status,
                "editor_revision": revision,
                "next_attempt_at": None,
            },
        )

    def write_drive_state(
        self,
        directory: Path,
        revision: int,
        *,
        status: str,
        srt_status: str,
        txt_status: str,
        srt_phase: str | None = None,
        txt_phase: str | None = None,
    ) -> None:
        self.write_json(
            directory
            / "editor-publish"
            / f"revision-{revision}"
            / "drive-publish-state.json",
            {
                "version": 2,
                "status": status,
                "files": {
                    "srt": {
                        "status": srt_status,
                        "phase": srt_phase
                        or ("completed" if srt_status == "completed" else "pending_upload"),
                    },
                    "txt": {
                        "status": txt_status,
                        "phase": txt_phase
                        or ("completed" if txt_status == "completed" else "pending_upload"),
                    },
                },
            },
        )

    def test_idle_revision_zero_allows_first_publish(self) -> None:
        self.create_job()

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["current_revision"], 0)
        self.assertIsNone(result["published_revision"])
        self.assertTrue(result["can_publish"])
        self.assertFalse(result["can_retry"])

    def test_current_intent_is_publishing(self) -> None:
        directory = self.create_job()
        self.write_marker(directory, "editor_publish_in_progress", 0)
        self.write_drive_state(
            directory,
            0,
            status="in_progress",
            srt_status="uploading",
            txt_status="pending",
        )

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "publishing")
        self.assertFalse(result["can_publish"])
        self.assertFalse(result["can_retry"])

    def test_completed_requires_consistent_current_revision_evidence(self) -> None:
        history = [
            {
                "revision": 0,
                "published_snapshot_revision": 0,
                "type": "drive_publish",
                "zero_edit_review": True,
            }
        ]
        directory = self.create_job(
            job_status="completed",
            batch_status="completed",
            history=history,
        )
        self.add_event("job-1", 0)
        self.write_marker(directory, "superseded_by_editor", 0)
        self.write_drive_state(
            directory,
            0,
            status="completed",
            srt_status="completed",
            txt_status="completed",
        )

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["published_revision"], 0)
        self.assertTrue(result["zero_edit_review"])
        self.assertFalse(result["can_publish"])

    def test_older_completed_revision_does_not_hide_new_revision(self) -> None:
        history = [
            {
                "revision": 0,
                "published_snapshot_revision": 0,
                "type": "drive_publish",
            },
            {"revision": 1, "type": "segment_edit"},
        ]
        directory = self.create_job(
            job_status="completed",
            batch_status="completed",
            revision=1,
            history=history,
        )
        self.add_event("job-1", 0)
        self.write_marker(directory, "superseded_by_editor", 0)
        self.write_drive_state(
            directory,
            0,
            status="completed",
            srt_status="completed",
            txt_status="completed",
        )

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["current_revision"], 1)
        self.assertEqual(result["published_revision"], 0)
        self.assertTrue(result["revision_changed_during_publish"])
        self.assertTrue(result["can_publish"])

    def test_explicit_failure_is_not_inferred_safe_to_retry(self) -> None:
        directory = self.create_job()
        self.write_marker(directory, "editor_publish_failed", 0)
        self.write_drive_state(
            directory,
            0,
            status="failed",
            srt_status="failed",
            txt_status="pending",
        )

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["can_publish"])
        self.assertFalse(result["can_retry"])

    def test_partial_or_contradictory_terminal_state_is_ambiguous(self) -> None:
        history = [
            {
                "revision": 0,
                "published_snapshot_revision": 0,
                "type": "drive_publish",
            }
        ]
        directory = self.create_job(
            job_status="completed",
            batch_status="completed",
            history=history,
        )
        self.add_event("job-1", 0)
        self.write_marker(directory, "superseded_by_editor", 0)
        self.write_drive_state(
            directory,
            0,
            status="failed",
            srt_status="completed",
            txt_status="failed",
        )

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "ambiguous")
        self.assertFalse(result["can_publish"])
        self.assertFalse(result["can_retry"])

    def test_malformed_state_fails_closed_without_500(self) -> None:
        directory = self.create_job()
        (directory / "drive-delivery-state.json").write_text("{broken", encoding="utf-8")

        result = get_publish_status("job-1")

        self.assertEqual(result["status"], "ambiguous")
        self.assertFalse(result["can_publish"])
        self.assertFalse(result["can_retry"])

    def test_missing_subtitle_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            get_publish_status("missing")
        self.assertEqual(raised.exception.status_code, 404)

    def test_status_reads_do_not_modify_database_or_job_files(self) -> None:
        directory = self.create_job()
        tracked = [self.database, *sorted(directory.rglob("*"))]

        def snapshot() -> dict[str, tuple[int, int, str] | None]:
            result: dict[str, tuple[int, int, str] | None] = {}
            for path in tracked:
                if not path.is_file():
                    result[str(path)] = None
                    continue
                stat = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                result[str(path)] = (stat.st_size, stat.st_mtime_ns, digest)
            return result

        before_files = snapshot()
        with sqlite3.connect(self.database) as connection:
            before_rows = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("jobs", "batches", "job_events")
            }

        for _ in range(3):
            self.assertEqual(get_publish_status("job-1")["status"], "idle")

        with sqlite3.connect(self.database) as connection:
            after_rows = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("jobs", "batches", "job_events")
            }
        self.assertEqual(before_rows, after_rows)
        self.assertEqual(before_files, snapshot())


if __name__ == "__main__":
    unittest.main()
