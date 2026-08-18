from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.review.auth as auth
import app.review.contributions as contributions
from app.review.auth_store import ReviewAuthStore
from app.review.store import ReviewStore


class ReviewContributionApiTests(unittest.TestCase):
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

        self.original_auth_dir = auth.DATA_DIR
        self.original_contribution_dir = contributions.DATA_DIR
        auth.DATA_DIR = self.data_dir
        contributions.DATA_DIR = self.data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        contributions._store_cache = None
        self.addCleanup(self._restore)

        self.store = ReviewStore(self.data_dir / "course-transcript.db")
        self.store.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 集",
            duration_ms=10000,
        )
        self.segments = self.store.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5000, "end_ms": 10000, "text": "彌勒大成佛今"},
            ],
        )
        self.me = self.store.get_or_create_user_for_identity(
            provider="google",
            provider_subject="me",
            display_name="法專師姐",
        )
        other = self.store.get_or_create_user_for_identity(
            provider="google",
            provider_subject="other",
            display_name="法明師兄",
        )
        self.primary_suggestion = self.store.submit_suggestion(
            segment_id=self.segments[1]["id"],
            user_id=self.me["id"],
            suggested_text="彌勒大成佛經",
        )
        self.store.submit_suggestion(
            segment_id=self.segments[0]["id"],
            user_id=other["id"],
            suggested_text="佛告阿難尊者",
        )
        self.store.update_progress(
            user_id=self.me["id"],
            youtube_video_id="video-1",
            last_playback_ms=10000,
            reviewed_until_ms=10000,
            completed=True,
        )
        token = ReviewAuthStore(self.data_dir / "course-transcript.db").create_session(
            user_id=self.me["id"]
        )["token"]

        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(contributions.router)
        self.client = TestClient(app, base_url="https://review.example.test")
        self.client.cookies.set(auth.COOKIE_NAME, token)

    def _restore(self) -> None:
        auth.DATA_DIR = self.original_auth_dir
        contributions.DATA_DIR = self.original_contribution_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        contributions._store_cache = None

    def test_leaderboard_prioritizes_completed_review_and_marks_current_reviewer(self) -> None:
        response = self.client.get("/api/v1/review/contributions")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["leaderboard"]), 2)
        self.assertEqual([row["rank"] for row in body["leaderboard"]], [1, 2])
        mine = body["me"]
        self.assertIsNotNone(mine)
        self.assertTrue(mine["is_me"])
        self.assertEqual(mine["rank"], 1)
        self.assertEqual(mine["suggestions_sent"], 1)
        self.assertEqual(mine["changed_chars"], 1)
        self.assertEqual(mine["completed_videos"], 1)
        self.assertEqual(mine["approved_suggestions"], 0)
        self.assertEqual(mine["pending_suggestions"], 1)

    def test_my_detail_returns_per_video_contribution_and_review_states(self) -> None:
        response = self.client.get("/api/v1/review/contributions/me")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["suggestions_sent"], 1)
        self.assertEqual(body["videos_contributed"], 1)
        self.assertEqual(body["completed_videos"], 1)
        self.assertEqual(body["approved_suggestions"], 0)
        self.assertEqual(body["pending_suggestions"], 1)
        self.assertEqual(body["videos"][0]["youtube_video_id"], "video-1")
        self.assertEqual(body["videos"][0]["suggestions_sent"], 1)
        self.assertEqual(body["videos"][0]["pending_suggestions"], 1)

    def test_withdrawn_suggestion_does_not_inflate_contribution_totals(self) -> None:
        withdrawn = self.store.submit_suggestion(
            segment_id=self.segments[0]["id"],
            user_id=self.me["id"],
            suggested_text="佛告阿難尊者",
        )
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as connection:
            connection.execute(
                """
                UPDATE review_suggestions
                SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (self.me["id"], now, now, withdrawn["id"]),
            )
            connection.execute(
                """
                INSERT INTO review_suggestion_events(
                    suggestion_id, event_type, actor_user_id, payload_json, created_at
                ) VALUES (?, 'withdrawn', ?, '{}', ?)
                """,
                (withdrawn["id"], self.me["id"], now),
            )

        board = self.client.get("/api/v1/review/contributions").json()["me"]
        detail = self.client.get("/api/v1/review/contributions/me").json()
        self.assertEqual(board["suggestions_sent"], 1)
        self.assertEqual(board["changed_chars"], 1)
        self.assertEqual(detail["suggestions_sent"], 1)
        self.assertEqual(detail["pending_suggestions"], 1)
        self.assertEqual(detail["videos"][0]["suggestions_sent"], 1)


if __name__ == "__main__":
    unittest.main()
