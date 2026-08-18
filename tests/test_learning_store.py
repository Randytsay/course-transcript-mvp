from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.learning.source import LearningSourceStore
from app.learning.store import LearningStore
from app.review.admin_store import ReviewAdminStore
from app.review.baseline import ensure_import_baseline
from app.review.store import ReviewConflict, ReviewStore


class LearningStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        self.review = ReviewStore(self.database)
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 講",
            duration_ms=60_000,
            caption_track_id="caption-1",
        )
        self.segments = self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "彌勒大成佛經"},
            ],
        )
        self.user = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="learner-1",
            display_name="學員一",
        )
        self.store = LearningStore(self.database)

    def test_learning_completion_is_separate_from_subtitle_review_completion(self) -> None:
        self.review.update_progress(
            user_id=self.user["id"],
            youtube_video_id="video-1",
            last_playback_ms=42_000,
            reviewed_until_ms=5_000,
            completed=False,
        )
        state = self.store.upsert_learning_state(
            user_id=self.user["id"],
            youtube_video_id="video-1",
            learning_status="completed",
        )
        self.assertEqual(state["learning_status"], "completed")
        dashboard = self.store.dashboard(user_id=self.user["id"])
        self.assertEqual(dashboard["summary"]["completed_count"], 1)
        self.assertEqual(dashboard["continue_learning"], None)
        with self.review.connect() as connection:
            review_progress = connection.execute(
                "SELECT * FROM review_video_progress WHERE user_id = ? AND youtube_video_id = ?",
                (self.user["id"], "video-1"),
            ).fetchone()
        self.assertEqual(review_progress["completed"], 0)
        self.assertEqual(review_progress["reviewed_until_ms"], 5_000)

    def test_notes_bookmarks_and_search_are_personal_and_timestamped(self) -> None:
        note = self.store.create_note(
            user_id=self.user["id"],
            youtube_video_id="video-1",
            title="重點",
            body="記得複習龍華三會",
            start_ms=5_000,
            segment_id=self.segments[1]["id"],
        )
        bookmark = self.store.create_bookmark(
            user_id=self.user["id"],
            youtube_video_id="video-1",
            start_ms=5_000,
            segment_id=self.segments[1]["id"],
            label="經名",
        )
        self.assertEqual(self.store.list_notes(user_id=self.user["id"])[0]["id"], note["id"])
        self.assertEqual(self.store.list_bookmarks(user_id=self.user["id"])[0]["id"], bookmark["id"])
        result = self.store.search(user_id=self.user["id"], query="彌勒")
        self.assertEqual(result["subtitle_results"][0]["start_ms"], 5_000)

        other = self.review.get_or_create_user_for_identity(
            provider="line", provider_subject="other", display_name="另一位學員"
        )
        with self.assertRaises(ReviewConflict):
            self.store.delete_note(user_id=other["id"], note_id=note["id"])
        with self.assertRaises(ReviewConflict):
            self.store.delete_bookmark(user_id=other["id"], bookmark_id=bookmark["id"])

    def test_completed_lesson_enters_spaced_review_queue(self) -> None:
        self.store.upsert_learning_state(
            user_id=self.user["id"],
            youtube_video_id="video-1",
            learning_status="completed",
        )
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE learning_review_schedule SET next_due_at = ? WHERE user_id = ? AND youtube_video_id = ?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), self.user["id"], "video-1"),
            )
        due = self.store.review_queue(user_id=self.user["id"])
        self.assertEqual(len(due), 1)
        before_stage = int(due[0]["stage"])
        reviewed = self.store.review_lesson(
            user_id=self.user["id"], youtube_video_id="video-1"
        )
        self.assertGreaterEqual(int(reviewed["stage"]), before_stage)
        self.assertGreater(datetime.fromisoformat(reviewed["next_due_at"]), datetime.now(UTC))

    def test_artifact_is_version_grounded_and_becomes_stale_after_new_version(self) -> None:
        admin = ReviewAdminStore(self.database)
        baseline = ensure_import_baseline(
            admin, youtube_video_id="video-1", triggered_by="owner@example.test"
        )
        source = LearningSourceStore(self.database)
        approved = source.approve_latest(
            youtube_video_id="video-1", actor="owner@example.test"
        )
        self.assertEqual(approved["subtitle_version_id"], baseline["id"])
        artifact = self.store.store_artifact(
            youtube_video_id="video-1",
            subtitle_version_id=baseline["id"],
            source_sha256=baseline["content_sha256"],
            artifact_type="study_pack",
            title="AI 學習整理",
            content={"key_points": [{"text": "彌勒", "source_segment_indexes": [2]}]},
            citations=[{"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "彌勒大成佛經"}],
            model="test-model",
            prompt_version="v1",
            actor="owner@example.test",
        )
        self.assertEqual(artifact["source_sha256"], baseline["content_sha256"])
        self.assertFalse(self.store.artifact_for_video("video-1")["is_stale"])

        # Create a later immutable version without changing the approved learning source.
        with admin.transaction() as connection:
            connection.execute(
                "UPDATE review_subtitle_segments SET working_text = ?, revision = revision + 1 WHERE youtube_video_id = ? AND segment_index = 2",
                ("彌勒大成佛經。", "video-1"),
            )
            latest = admin._snapshot_video(
                connection,
                youtube_video_id="video-1",
                actor="owner@example.test",
                source="test",
                source_ref=None,
            )
        self.assertNotEqual(latest["content_sha256"], baseline["content_sha256"])
        self.assertTrue(self.store.artifact_for_video("video-1")["is_stale"])
        status = source.status("video-1")
        self.assertFalse(status["source_is_latest"])


if __name__ == "__main__":
    unittest.main()
