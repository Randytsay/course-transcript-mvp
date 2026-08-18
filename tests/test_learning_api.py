from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.learning.routes as learning_routes
import app.review.auth as auth
from app.review.auth_store import ReviewAuthStore
from app.review.store import ReviewStore


class LearningApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.env = patch.dict(
            os.environ,
            {"REVIEW_PUBLIC_ORIGIN": "https://review.example.test"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.original_auth_data_dir = auth.DATA_DIR
        self.original_learning_data_dir = learning_routes.DATA_DIR
        auth.DATA_DIR = self.data_dir
        learning_routes.DATA_DIR = self.data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        learning_routes._store_cache = None
        self.addCleanup(self._restore_globals)

        self.review = ReviewStore(self.data_dir / "course-transcript.db")
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 講",
            duration_ms=60_000,
        )
        self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "彌勒大成佛經"},
            ],
        )
        self.user = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="learner-api",
            display_name="共學者",
        )
        session = ReviewAuthStore(self.data_dir / "course-transcript.db").create_session(
            user_id=self.user["id"]
        )
        self.session_token = session["token"]
        self.csrf = auth._csrf_for_token(self.session_token)

        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(learning_routes.router)
        self.client = TestClient(app, base_url="https://review.example.test")
        self.client.cookies.set(auth.COOKIE_NAME, self.session_token)

    def _restore_globals(self) -> None:
        auth.DATA_DIR = self.original_auth_data_dir
        learning_routes.DATA_DIR = self.original_learning_data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        learning_routes._store_cache = None

    def _headers(self) -> dict[str, str]:
        return {
            "Origin": "https://review.example.test",
            "X-Review-CSRF": self.csrf,
        }

    def test_dashboard_watch_state_notes_and_bookmarks_round_trip(self) -> None:
        initial = self.client.get("/api/v1/review/learning/dashboard")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["summary"]["video_count"], 1)
        self.assertEqual(initial.json()["summary"]["completed_count"], 0)

        watch = self.client.post(
            "/api/v1/review/learning/videos/video-1/watch",
            headers=self._headers(),
            json={"last_playback_ms": 12_000},
        )
        self.assertEqual(watch.status_code, 200)
        dashboard = self.client.get("/api/v1/review/learning/dashboard").json()
        self.assertEqual(dashboard["videos"][0]["learning_status"], "in_progress")
        self.assertEqual(dashboard["videos"][0]["last_playback_ms"], 12_000)
        self.assertEqual(dashboard["continue_learning"]["youtube_video_id"], "video-1")

        bookmark = self.client.post(
            "/api/v1/review/learning/videos/video-1/bookmarks",
            headers=self._headers(),
            json={"start_ms": 5_000, "label": "重要段落"},
        )
        self.assertEqual(bookmark.status_code, 200)
        note = self.client.post(
            "/api/v1/review/learning/videos/video-1/notes",
            headers=self._headers(),
            json={"body": "自己的理解", "title": "筆記", "start_ms": 5_000},
        )
        self.assertEqual(note.status_code, 200)

        lesson = self.client.get("/api/v1/review/learning/videos/video-1")
        self.assertEqual(lesson.status_code, 200)
        self.assertEqual(len(lesson.json()["bookmarks"]), 1)
        self.assertEqual(len(lesson.json()["notes"]), 1)
        self.assertIsNone(lesson.json()["artifact"])

        complete = self.client.post(
            "/api/v1/review/learning/videos/video-1/state",
            headers=self._headers(),
            json={"learning_status": "completed"},
        )
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["learning_state"]["learning_status"], "completed")
        completed_dashboard = self.client.get("/api/v1/review/learning/dashboard")
        self.assertEqual(completed_dashboard.status_code, 200)
        self.assertEqual(completed_dashboard.json()["summary"]["completed_count"], 1)
        self.assertIsNone(completed_dashboard.json()["continue_learning"])

        # Rewatching after explicit completion updates playback but does not
        # silently reopen the learner's completion state.
        rewatch = self.client.post(
            "/api/v1/review/learning/videos/video-1/watch",
            headers=self._headers(),
            json={"last_playback_ms": 30_000},
        )
        self.assertEqual(rewatch.status_code, 200)
        after_rewatch = self.client.get("/api/v1/review/learning/dashboard").json()
        self.assertEqual(after_rewatch["videos"][0]["learning_status"], "completed")
        self.assertEqual(after_rewatch["videos"][0]["last_playback_ms"], 30_000)
        self.assertIsNone(after_rewatch["continue_learning"])

    def test_learning_watch_does_not_clear_subtitle_review_completion(self) -> None:
        self.review.update_progress(
            user_id=self.user["id"],
            youtube_video_id="video-1",
            last_playback_ms=10_000,
            reviewed_until_ms=60_000,
            last_segment_index=2,
            completed=True,
        )
        watched = self.client.post(
            "/api/v1/review/learning/videos/video-1/watch",
            headers=self._headers(),
            json={"last_playback_ms": 25_000},
        )
        self.assertEqual(watched.status_code, 200)
        progress = watched.json()["progress"]
        self.assertEqual(progress["completed"], 1)
        self.assertEqual(progress["reviewed_until_ms"], 60_000)
        self.assertEqual(progress["last_playback_ms"], 25_000)
        dashboard = self.client.get("/api/v1/review/learning/dashboard").json()
        self.assertEqual(dashboard["videos"][0]["subtitle_review_completed"], 1)
        self.assertEqual(dashboard["videos"][0]["review_progress_ms"], 60_000)

    def test_mutations_require_reviewer_csrf_and_reads_require_session(self) -> None:
        bad = self.client.post(
            "/api/v1/review/learning/videos/video-1/state",
            json={"learning_status": "completed"},
        )
        self.assertEqual(bad.status_code, 403)
        self.client.cookies.clear()
        read = self.client.get("/api/v1/review/learning/dashboard")
        self.assertEqual(read.status_code, 401)

    def test_search_returns_clickable_timestamp_evidence(self) -> None:
        response = self.client.get("/api/v1/review/learning/search", params={"q": "彌勒"})
        self.assertEqual(response.status_code, 200)
        result = response.json()["subtitle_results"][0]
        self.assertEqual(result["youtube_video_id"], "video-1")
        self.assertEqual(result["start_ms"], 5_000)
        self.assertEqual(result["text"], "彌勒大成佛經")


if __name__ == "__main__":
    unittest.main()
