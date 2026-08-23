from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.learning.store import LearningStore
from app.learning.source import LearningSourceStore
from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewStore
from scripts.reconcile_learning_source_segments import _apply, _connect, _plans


class LearningSourceReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        self.review = ReviewStore(self.database)
        LearningStore(self.database)
        LearningSourceStore(self.database)
        self.admin = ReviewAdminStore(self.database)
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="第一堂",
            duration_ms=5000,
            caption_track_id="caption-1",
        )
        self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[{"segment_index": 1, "start_ms": 0, "end_ms": 5000, "text": "舊字幕"}],
        )
        source = [
            {"segment_index": 1, "start_ms": 0, "end_ms": 2000, "working_text": "正式第一段"},
            {"segment_index": 2, "start_ms": 2000, "end_ms": 5000, "working_text": "正式第二段"},
        ]
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT INTO review_subtitle_versions(
                    id, youtube_video_id, version_number, source, snapshot_json,
                    srt_text, content_sha256, created_by_actor, created_at
                ) VALUES (?, ?, 1, 'imported', ?, '正式 SRT', 'sha256:test', 'test', 'now')
                """,
                ("version-1", "video-1", json.dumps(source, ensure_ascii=False)),
            )
            connection.execute(
                """
                INSERT INTO learning_source_versions(
                    youtube_video_id, subtitle_version_id, source_sha256,
                    approved_by_actor, approved_at
                ) VALUES ('video-1', 'version-1', 'sha256:test', 'test', 'now')
                """
            )

    def test_reconciles_rows_to_approved_snapshot(self) -> None:
        connection = _connect(self.database)
        try:
            plans = _plans(connection)
            self.assertEqual(len(plans), 1)
            self.assertEqual((plans[0].current_count, plans[0].source_count), (1, 2))
            connection.execute("BEGIN IMMEDIATE")
            _apply(connection, plans[0], "test")
            connection.commit()
        finally:
            connection.close()

        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """
                SELECT segment_index, start_ms, end_ms, working_text
                FROM review_subtitle_segments
                WHERE youtube_video_id = 'video-1' ORDER BY segment_index
                """
            ).fetchall()
            audit = connection.execute(
                "SELECT action FROM review_admin_audit WHERE entity_id = 'video-1'"
            ).fetchone()
        self.assertEqual(rows, [(1, 0, 2000, "正式第一段"), (2, 2000, 5000, "正式第二段")])
        self.assertEqual(audit[0], "learning_source_segments_reconciled")


if __name__ == "__main__":
    unittest.main()
