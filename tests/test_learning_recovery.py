from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.learning.recovery import recover_stale_generation_jobs
from app.learning.store import LearningStore
from app.review.admin_store import ReviewAdminStore
from app.review.baseline import ensure_import_baseline
from app.review.store import ReviewStore


class LearningRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        review = ReviewStore(self.database)
        review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 講",
            duration_ms=20_000,
        )
        review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[{"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"}],
        )
        self.version = ensure_import_baseline(
            ReviewAdminStore(self.database),
            youtube_video_id="video-1",
            triggered_by="owner@example.test",
        )
        self.store = LearningStore(self.database)

    def _insert_running(self, *, job_id: str, started_at: datetime) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO learning_generation_jobs(
                    id, youtube_video_id, subtitle_version_id, artifact_type,
                    prompt_version, model, status, actor, started_at
                ) VALUES (?, 'video-1', ?, 'study_pack', 'learning-study-pack-v2',
                          'test-model', 'running', 'owner@example.test', ?)
                """,
                (job_id, self.version["id"], started_at.isoformat()),
            )

    def test_only_abandoned_generation_jobs_are_failed(self) -> None:
        now = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
        self._insert_running(job_id="stale-job", started_at=now - timedelta(minutes=90))
        self._insert_running(job_id="recent-job", started_at=now - timedelta(minutes=10))
        with patch.dict("os.environ", {"LEARNING_GENERATION_STALE_MINUTES": "60"}, clear=False):
            recovered = recover_stale_generation_jobs(
                self.store,
                youtube_video_id="video-1",
                now=now,
            )
        self.assertEqual(recovered, 1)
        rows = {row["id"]: row for row in self.store.list_generation_jobs()}
        self.assertEqual(rows["stale-job"]["status"], "failed")
        self.assertIsNotNone(rows["stale-job"]["finished_at"])
        self.assertIn("逾時或中斷", rows["stale-job"]["error"])
        self.assertEqual(rows["recent-job"]["status"], "running")
        self.assertIsNone(rows["recent-job"]["finished_at"])

    def test_stale_threshold_is_bounded_to_at_least_fifteen_minutes(self) -> None:
        now = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
        self._insert_running(job_id="ten-minutes", started_at=now - timedelta(minutes=10))
        self._insert_running(job_id="twenty-minutes", started_at=now - timedelta(minutes=20))
        with patch.dict("os.environ", {"LEARNING_GENERATION_STALE_MINUTES": "1"}, clear=False):
            recovered = recover_stale_generation_jobs(self.store, now=now)
        self.assertEqual(recovered, 1)
        rows = {row["id"]: row for row in self.store.list_generation_jobs()}
        self.assertEqual(rows["ten-minutes"]["status"], "running")
        self.assertEqual(rows["twenty-minutes"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
