from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.learning.source import LearningSourceStore
from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewStore


class LearningSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        review = ReviewStore(self.database)
        review.upsert_video(
            youtube_video_id="video-clean",
            playlist_id="playlist-1",
            title="已確認但不需修改的課程",
            duration_ms=10_000,
        )
        review.import_subtitle_segments(
            youtube_video_id="video-clean",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "字幕已確認正確"},
            ],
        )
        self.admin = ReviewAdminStore(self.database)
        self.source = LearningSourceStore(self.database)

    def test_explicit_source_approval_freezes_v1_when_video_has_no_versions(self) -> None:
        self.assertEqual(self.admin.list_versions(youtube_video_id="video-clean"), [])
        approved = self.source.approve_latest(
            youtube_video_id="video-clean",
            actor="owner@example.test",
        )
        versions = self.admin.list_versions(youtube_video_id="video-clean")
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version_number"], 1)
        self.assertEqual(versions[0]["source"], "import_baseline")
        self.assertEqual(approved["subtitle_version_id"], versions[0]["id"])
        self.assertEqual(approved["source_sha256"], versions[0]["content_sha256"])
        self.assertEqual(approved["approved_by_actor"], "owner@example.test")

    def test_reapproving_same_latest_version_is_idempotent_for_version_history(self) -> None:
        first = self.source.approve_latest(
            youtube_video_id="video-clean",
            actor="owner@example.test",
        )
        second = self.source.approve_latest(
            youtube_video_id="video-clean",
            actor="owner2@example.test",
        )
        self.assertEqual(first["subtitle_version_id"], second["subtitle_version_id"])
        self.assertEqual(len(self.admin.list_versions(youtube_video_id="video-clean")), 1)
        self.assertEqual(second["approved_by_actor"], "owner2@example.test")


if __name__ == "__main__":
    unittest.main()
