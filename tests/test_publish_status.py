from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class PublishStatusTests(unittest.TestCase):
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

        (self.data / "jobs").mkdir(parents=True)
        (self.data / "imported").mkdir(parents=True)

        import app.subtitles.editor as base
        self.orig_data_dir = base.DATA_DIR
        self.orig_jobs_dir = base.JOBS_DIR
        self.orig_imported_dir = base.IMPORTED_DIR

        base.DATA_DIR = self.data
        base.JOBS_DIR = self.data / "jobs"
        base.IMPORTED_DIR = self.data / "imported"

        self.db_path = self.data / "course-transcript.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT,
                    status TEXT,
                    active_stage TEXT,
                    stage_detail TEXT,
                    progress INTEGER,
                    error TEXT,
                    updated_at TEXT,
                    revision INTEGER,
                    source_path TEXT,
                    source_name TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE batches (
                    id TEXT PRIMARY KEY,
                    status TEXT,
                    completed_count INTEGER,
                    failed_count INTEGER,
                    updated_at TEXT,
                    revision INTEGER
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    event_type TEXT,
                    actor TEXT,
                    payload_json TEXT,
                    created_at TEXT
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        from app.api_hardened import app as api_app
        self.client = TestClient(api_app)

    def tearDown(self) -> None:
        import app.subtitles.editor as base
        base.DATA_DIR = self.orig_data_dir
        base.JOBS_DIR = self.orig_jobs_dir
        base.IMPORTED_DIR = self.orig_imported_dir

        self.tmp.cleanup()
        self.access_env.stop()

    def _create_job_files(self, job_id: str, revision: int = 1) -> Path:
        job_dir = self.data / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "subtitle-editor.json").write_text(
            json.dumps({"revision": revision, "edits": {}, "history": []}),
            encoding="utf-8"
        )
        (job_dir / "subtitles.json").write_text(
            json.dumps({"segments": [{"segment_id": "seg-1", "start_ms": 100, "end_ms": 900, "raw_text": "hello"}]}),
            encoding="utf-8"
        )
        return job_dir

    def _insert_db_job(
        self, job_id: str, status: str, batch_id: str | None = None
    ) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO jobs (id, batch_id, status, active_stage, revision, source_path, source_name)
                VALUES (?, ?, ?, 'review', 1, 'gdrive:course/lesson.mp3', 'lesson.mp3')
                """,
                (job_id, batch_id, status),
            )
            if batch_id:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO batches (id, status, completed_count, failed_count, revision)
                    VALUES (?, 'processing', 0, 0, 1)
                    """,
                    (batch_id,),
                )
            connection.commit()
        finally:
            connection.close()

    def _insert_db_event(self, job_id: str, event_type: str) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, 'editor', '{}', '2026-08-06T12:00:00Z')
                """,
                (job_id, event_type),
            )
            connection.commit()
        finally:
            connection.close()

    def test_nonexistent_subtitle_404(self) -> None:
        response = self.client.get("/api/v1/subtitles/nonexistent/publish-status")
        self.assertEqual(response.status_code, 404)

    def test_imported_subtitle_409(self) -> None:
        imp_dir = self.data / "imported" / "imp-1"
        imp_dir.mkdir(parents=True)
        (imp_dir / "subtitle-editor.json").write_text(
            json.dumps({"revision": 1, "edits": {}, "history": []}),
            encoding="utf-8"
        )
        response = self.client.get("/api/v1/subtitles/imp-1/publish-status")
        self.assertEqual(response.status_code, 409)

    def test_idle_status_can_publish(self) -> None:
        job_id = "job-idle"
        self._create_job_files(job_id)
        self._insert_db_job(job_id, "awaiting_review")

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["can_publish"])
        self.assertFalse(payload["can_retry"])

    def test_publishing_status(self) -> None:
        job_id = "job-pub"
        job_dir = self._create_job_files(job_id)
        self._insert_db_job(job_id, "awaiting_review")

        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({
                "status": "editor_publish_in_progress",
                "editor_revision": 1,
                "actor": "user"
            }),
            encoding="utf-8"
        )

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "publishing")
        self.assertFalse(payload["can_publish"])

    def test_completed_status_with_strict_files_check(self) -> None:
        job_id = "job-comp"
        job_dir = self._create_job_files(job_id, revision=1)
        self._insert_db_job(job_id, "completed", batch_id="batch-1")
        self._insert_db_event(job_id, "job_drive_editor_published")

        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({
                "status": "superseded_by_editor",
                "editor_revision": 1,
                "actor": "user"
            }),
            encoding="utf-8"
        )

        pub_rev_dir = job_dir / "editor-publish" / "revision-1"
        pub_rev_dir.mkdir(parents=True)
        (pub_rev_dir / "drive-publish-state.json").write_text(
            json.dumps({
                "status": "completed",
                "files": {
                    "srt": {"status": "completed"},
                    "txt": {"status": "completed"}
                }
            }),
            encoding="utf-8"
        )

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["published_revision"], 1)
        self.assertEqual(payload["current_revision"], 1)
        self.assertFalse(payload["can_publish"])

    def test_completed_older_revision_allows_new_publish(self) -> None:
        job_id = "job-comp-old"
        job_dir = self._create_job_files(job_id, revision=2)
        self._insert_db_job(job_id, "completed")
        self._insert_db_event(job_id, "job_drive_editor_published")

        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({
                "status": "superseded_by_editor",
                "editor_revision": 1,
                "actor": "user"
            }),
            encoding="utf-8"
        )

        pub_rev_dir = job_dir / "editor-publish" / "revision-1"
        pub_rev_dir.mkdir(parents=True)
        (pub_rev_dir / "drive-publish-state.json").write_text(
            json.dumps({
                "status": "completed",
                "files": {
                    "srt": {"status": "completed"},
                    "txt": {"status": "completed"}
                }
            }),
            encoding="utf-8"
        )

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["published_revision"], 1)
        self.assertEqual(payload["current_revision"], 2)
        self.assertTrue(payload["revision_changed_during_publish"])
        self.assertTrue(payload["can_publish"])

    def test_failed_not_retryable_by_default(self) -> None:
        job_id = "job-fail-default"
        job_dir = self._create_job_files(job_id)
        self._insert_db_job(job_id, "awaiting_review")

        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({
                "status": "editor_publish_failed",
                "editor_revision": 1,
                "actor": "user"
            }),
            encoding="utf-8"
        )

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertFalse(payload["can_retry"])
        self.assertFalse(payload["can_publish"])

    def test_failed_retryable_when_explicitly_safe(self) -> None:
        job_id = "job-fail-safe"
        job_dir = self._create_job_files(job_id)
        self._insert_db_job(job_id, "awaiting_review")

        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({
                "status": "editor_publish_failed",
                "editor_revision": 1,
                "actor": "user"
            }),
            encoding="utf-8"
        )

        pub_rev_dir = job_dir / "editor-publish" / "revision-1"
        pub_rev_dir.mkdir(parents=True)
        (pub_rev_dir / "drive-publish-state.json").write_text(
            json.dumps({
                "status": "failed",
                "safe_to_retry": True,
                "remote_mutation_started": False
            }),
            encoding="utf-8"
        )

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(payload["can_retry"])
        self.assertTrue(payload["can_publish"])

    def test_malformed_json_fails_closed_without_500(self) -> None:
        job_id = "job-malformed"
        job_dir = self._create_job_files(job_id)
        (job_dir / "drive-delivery-state.json").write_text("{invalid json", encoding="utf-8")

        response = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["can_publish"])
        self.assertFalse(payload["can_retry"])

    def test_endpoint_is_strictly_read_only(self) -> None:
        import hashlib

        job_id = "job-ro-check"
        job_dir = self._create_job_files(job_id)
        self._insert_db_job(job_id, "completed")
        self._insert_db_event(job_id, "job_drive_editor_published")

        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({
                "status": "superseded_by_editor",
                "editor_revision": 1,
                "actor": "user"
            }),
            encoding="utf-8"
        )

        def file_hash(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        db_hash_before = file_hash(self.db_path)
        job_file_hashes_before = {
            p.name: file_hash(p) for p in job_dir.glob("*") if p.is_file()
        }

        for _ in range(5):
            res = self.client.get(f"/api/v1/subtitles/{job_id}/publish-status")
            self.assertEqual(res.status_code, 200)

        db_hash_after = file_hash(self.db_path)
        job_file_hashes_after = {
            p.name: file_hash(p) for p in job_dir.glob("*") if p.is_file()
        }

        self.assertEqual(db_hash_before, db_hash_after)
        self.assertEqual(job_file_hashes_before, job_file_hashes_after)


if __name__ == "__main__":
    unittest.main()
