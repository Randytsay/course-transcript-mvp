from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.review.auth as auth
import app.review.portal as portal
from app.review.auth_store import ReviewAuthStore
from app.review.lease_store import ReviewLeaseStore
from app.review.store import ReviewConflict, ReviewStore


class ReviewLeaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        self.review = ReviewStore(self.database)
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 集",
            duration_ms=60000,
        )
        self.users = [
            self.review.get_or_create_user_for_identity(
                provider="google",
                provider_subject=f"google-{index}",
                display_name=f"校訂者 {index}",
            )
            for index in range(1, 4)
        ]
        self.leases = ReviewLeaseStore(self.database, max_editors_per_video=2)

    def test_only_two_reviewers_can_hold_active_video_lease(self) -> None:
        first = self.leases.acquire(user_id=self.users[0]["id"], youtube_video_id="video-1")
        second = self.leases.acquire(user_id=self.users[1]["id"], youtube_video_id="video-1")
        self.assertTrue(first["lease_token"])
        self.assertTrue(second["lease_token"])
        with self.assertRaises(ReviewConflict):
            self.leases.acquire(user_id=self.users[2]["id"], youtube_video_id="video-1")

        self.assertTrue(
            self.leases.release(
                user_id=self.users[0]["id"],
                youtube_video_id="video-1",
                lease_token=first["lease_token"],
            )
        )
        third = self.leases.acquire(user_id=self.users[2]["id"], youtube_video_id="video-1")
        self.assertTrue(third["lease_token"])

    def test_expired_lease_does_not_block_next_reviewer(self) -> None:
        first = self.leases.acquire(user_id=self.users[0]["id"], youtube_video_id="video-1")
        self.leases.acquire(user_id=self.users[1]["id"], youtube_video_id="video-1")
        with self.leases.transaction() as connection:
            connection.execute(
                "UPDATE review_edit_leases SET expires_at = ? WHERE token_hash IS NOT NULL AND user_id = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), self.users[0]["id"]),
            )
        self.assertFalse(
            self.leases.validate(
                user_id=self.users[0]["id"],
                youtube_video_id="video-1",
                lease_token=first["lease_token"],
            )
        )
        third = self.leases.acquire(user_id=self.users[2]["id"], youtube_video_id="video-1")
        self.assertTrue(third["lease_token"])


class ReviewPortalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.env = patch.dict(
            os.environ,
            {
                "REVIEW_PUBLIC_ORIGIN": "https://review.example.test",
                "REVIEW_MAX_EDITORS_PER_VIDEO": "2",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.original_auth_data_dir = auth.DATA_DIR
        self.original_portal_data_dir = portal.DATA_DIR
        auth.DATA_DIR = self.data_dir
        portal.DATA_DIR = self.data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        portal._review_store_cache = None
        portal._lease_store_cache = None
        self.addCleanup(self._restore_globals)

        self.review = ReviewStore(self.data_dir / "course-transcript.db")
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 集",
            duration_ms=60000,
            caption_track_id="caption-1",
        )
        self.segments = self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5000, "end_ms": 10000, "text": "彌勒大成佛今"},
            ],
        )
        self.user = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="google-primary",
            display_name="法專師姐",
        )
        session = ReviewAuthStore(self.data_dir / "course-transcript.db").create_session(
            user_id=self.user["id"]
        )
        self.session_token = session["token"]
        self.csrf = auth._csrf_for_token(self.session_token)

        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(portal.router)
        self.client = TestClient(app, base_url="https://review.example.test")
        self.client.cookies.set(auth.COOKIE_NAME, self.session_token)

    def _restore_globals(self) -> None:
        auth.DATA_DIR = self.original_auth_data_dir
        portal.DATA_DIR = self.original_portal_data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        portal._review_store_cache = None
        portal._lease_store_cache = None

    def _mutation_headers(self, **extra: str) -> dict[str, str]:
        return {
            "Origin": "https://review.example.test",
            "X-Review-CSRF": self.csrf,
            **extra,
        }

    def _acquire_lease(self) -> str:
        lease = self.client.post(
            "/api/v1/review/videos/video-1/lease",
            headers=self._mutation_headers(),
        )
        self.assertEqual(lease.status_code, 200)
        return str(lease.json()["lease_token"])

    def test_video_list_and_detail_include_resume_and_fixed_segments(self) -> None:
        listing = self.client.get("/api/v1/review/videos")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["videos"][0]["segment_count"], 2)
        self.assertEqual(listing.json()["max_editors_per_video"], 2)
        self.assertEqual(listing.json()["videos"][0]["my_pending_count"], 0)
        self.assertEqual(listing.json()["videos"][0]["my_approved_count"], 0)

        detail = self.client.get("/api/v1/review/videos/video-1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["video"]["youtube_video_id"], "video-1")
        self.assertEqual(detail.json()["segments"][1]["working_text"], "彌勒大成佛今")
        self.assertEqual(detail.json()["segments"][1]["start_ms"], 5000)
        self.assertIsNone(detail.json()["segments"][1]["my_suggestion_status"])

    def test_video_detail_includes_named_contributors_with_avatar(self) -> None:
        other = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="google-contributor",
            display_name="共修師兄",
            avatar_url="https://example.test/avatar.png",
        )
        self.review.submit_suggestion(
            segment_id=self.segments[0]["id"],
            user_id=other["id"],
            suggested_text="佛告阿難尊者",
        )

        detail = self.client.get("/api/v1/review/videos/video-1")
        self.assertEqual(detail.status_code, 200)
        contributors = detail.json()["contributors"]
        self.assertEqual(len(contributors), 1)
        self.assertEqual(contributors[0]["display_name"], "共修師兄")
        self.assertEqual(contributors[0]["avatar_url"], "https://example.test/avatar.png")
        self.assertEqual(contributors[0]["suggestions_sent"], 1)

    def test_progress_is_saved_without_consuming_editor_slot(self) -> None:
        response = self.client.post(
            "/api/v1/review/videos/video-1/progress",
            headers=self._mutation_headers(),
            json={
                "last_playback_ms": 8500,
                "reviewed_until_ms": 5000,
                "last_segment_index": 2,
                "completed": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["progress"]["last_playback_ms"], 8500)
        listing = self.client.get("/api/v1/review/videos").json()
        self.assertEqual(listing["resume"]["last_playback_ms"], 8500)
        self.assertEqual(listing["videos"][0]["reviewed_until_ms"], 5000)
        self.assertEqual(listing["videos"][0]["active_editor_count"], 0)

    def test_completed_review_is_visible_in_library(self) -> None:
        response = self.client.post(
            "/api/v1/review/videos/video-1/progress",
            headers=self._mutation_headers(),
            json={
                "last_playback_ms": 10000,
                "reviewed_until_ms": 60000,
                "last_segment_index": 2,
                "completed": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        listing = self.client.get("/api/v1/review/videos").json()["videos"][0]
        self.assertTrue(listing["completed"])
        self.assertEqual(listing["reviewed_until_ms"], 60000)

    def test_explicit_completion_control_can_reopen_without_losing_progress(self) -> None:
        completed = self.client.post(
            "/api/v1/review/videos/video-1/progress",
            headers=self._mutation_headers(),
            json={
                "last_playback_ms": 9000,
                "reviewed_until_ms": 10000,
                "last_segment_index": 2,
                "completed": True,
            },
        )
        self.assertEqual(completed.status_code, 200)

        reopened = self.client.post(
            "/api/v1/review/videos/video-1/progress/completion",
            headers=self._mutation_headers(),
            json={"completed": False},
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()["progress"]["completed"], 0)
        self.assertEqual(reopened.json()["progress"]["reviewed_until_ms"], 10000)
        self.assertEqual(reopened.json()["progress"]["last_playback_ms"], 9000)

    def test_suggestion_requires_lease_and_revises_same_pending_record(self) -> None:
        segment_id = self.segments[1]["id"]
        rejected = self.client.post(
            f"/api/v1/review/videos/video-1/segments/{segment_id}/suggestion",
            headers=self._mutation_headers(),
            json={"text": "彌勒大成佛經"},
        )
        self.assertEqual(rejected.status_code, 409)

        lease_token = self._acquire_lease()
        created = self.client.post(
            f"/api/v1/review/videos/video-1/segments/{segment_id}/suggestion",
            headers=self._mutation_headers(**{"X-Review-Lease": lease_token}),
            json={"text": "彌勒大成佛經"},
        )
        self.assertEqual(created.status_code, 200)
        self.assertTrue(created.json()["created"])

        revised = self.client.post(
            f"/api/v1/review/videos/video-1/segments/{segment_id}/suggestion",
            headers=self._mutation_headers(**{"X-Review-Lease": lease_token}),
            json={"text": "彌勒大成佛經。"},
        )
        self.assertEqual(revised.status_code, 200)
        self.assertFalse(revised.json()["created"])

        leaderboard = self.review.contribution_leaderboard()
        self.assertEqual(leaderboard[0]["suggestions_sent"], 1)
        self.assertEqual(leaderboard[0]["changed_chars"], 2)
        detail = self.client.get("/api/v1/review/videos/video-1").json()
        self.assertEqual(detail["segments"][1]["my_suggested_text"], "彌勒大成佛經。")
        self.assertEqual(detail["segments"][1]["my_suggestion_status"], "pending")
        listing = self.client.get("/api/v1/review/videos").json()["videos"][0]
        self.assertEqual(listing["my_suggestion_count"], 1)
        self.assertEqual(listing["my_pending_count"], 1)

    def test_batch_replace_requires_lease_and_keeps_formal_text_unchanged(self) -> None:
        with self.review.transaction() as connection:
            connection.execute(
                "UPDATE review_subtitle_segments SET working_text = ? WHERE id = ?",
                ("共同錯誤 佛", self.segments[0]["id"]),
            )
            connection.execute(
                "UPDATE review_subtitle_segments SET working_text = ? WHERE id = ?",
                ("共同錯誤 佛 彌勒", self.segments[1]["id"]),
            )

        without_lease = self.client.post(
            "/api/v1/review/videos/video-1/batch-suggestion",
            headers=self._mutation_headers(),
            json={"find_text": "佛", "replace_text": "佛陀"},
        )
        self.assertEqual(without_lease.status_code, 409)

        lease_token = self._acquire_lease()
        response = self.client.post(
            "/api/v1/review/videos/video-1/batch-suggestion",
            headers=self._mutation_headers(**{"X-Review-Lease": lease_token}),
            json={"find_text": "佛", "replace_text": "佛陀"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["batch"]["matched_count"], 2)
        self.assertEqual(response.json()["batch"]["created_count"], 2)
        detail = self.client.get("/api/v1/review/videos/video-1").json()
        self.assertEqual(detail["segments"][0]["working_text"], "共同錯誤 佛")
        self.assertEqual(detail["segments"][0]["my_suggested_text"], "共同錯誤 佛陀")
        self.assertEqual(detail["segments"][1]["my_suggested_text"], "共同錯誤 佛陀 彌勒")

    def test_pending_suggestion_can_be_withdrawn_without_delete(self) -> None:
        segment_id = self.segments[1]["id"]
        lease_token = self._acquire_lease()
        created = self.client.post(
            f"/api/v1/review/videos/video-1/segments/{segment_id}/suggestion",
            headers=self._mutation_headers(**{"X-Review-Lease": lease_token}),
            json={"text": "彌勒大成佛經"},
        )
        self.assertEqual(created.status_code, 200)
        suggestion_id = created.json()["suggestion"]["id"]

        history_before = self.client.get("/api/v1/review/suggestions/me")
        self.assertEqual(history_before.status_code, 200)
        self.assertEqual(history_before.json()["suggestions"][0]["display_status"], "pending")

        withdrawn = self.client.post(
            f"/api/v1/review/suggestions/{suggestion_id}/withdraw",
            headers=self._mutation_headers(),
        )
        self.assertEqual(withdrawn.status_code, 200)
        self.assertTrue(withdrawn.json()["withdrawn"])

        detail = self.client.get("/api/v1/review/videos/video-1").json()
        self.assertEqual(detail["segments"][1]["my_suggestion_status"], "withdrawn")
        history_after = self.client.get("/api/v1/review/suggestions/me").json()["suggestions"]
        self.assertEqual(history_after[0]["display_status"], "withdrawn")
        listing = self.client.get("/api/v1/review/videos").json()["videos"][0]
        self.assertEqual(listing["my_suggestion_count"], 0)
        self.assertEqual(listing["my_pending_count"], 0)

        with self.review.connect() as connection:
            stored = connection.execute(
                "SELECT status FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            event = connection.execute(
                "SELECT event_type FROM review_suggestion_events WHERE suggestion_id = ? ORDER BY id DESC LIMIT 1",
                (suggestion_id,),
            ).fetchone()
        self.assertEqual(stored["status"], "rejected")
        self.assertEqual(event["event_type"], "withdrawn")

    def test_third_reviewer_gets_conflict_until_slot_is_released(self) -> None:
        first = self.client.post(
            "/api/v1/review/videos/video-1/lease",
            headers=self._mutation_headers(),
        ).json()

        other_tokens: list[tuple[str, str]] = []
        for index in (2, 3):
            user = self.review.get_or_create_user_for_identity(
                provider="google",
                provider_subject=f"google-{index}",
                display_name=f"校訂者 {index}",
            )
            session = ReviewAuthStore(self.data_dir / "course-transcript.db").create_session(
                user_id=user["id"]
            )
            other_tokens.append((session["token"], auth._csrf_for_token(session["token"])))

        second_client = TestClient(self.client.app, base_url="https://review.example.test")
        second_client.cookies.set(auth.COOKIE_NAME, other_tokens[0][0])
        second = second_client.post(
            "/api/v1/review/videos/video-1/lease",
            headers={
                "Origin": "https://review.example.test",
                "X-Review-CSRF": other_tokens[0][1],
            },
        )
        self.assertEqual(second.status_code, 200)

        third_client = TestClient(self.client.app, base_url="https://review.example.test")
        third_client.cookies.set(auth.COOKIE_NAME, other_tokens[1][0])
        blocked = third_client.post(
            "/api/v1/review/videos/video-1/lease",
            headers={
                "Origin": "https://review.example.test",
                "X-Review-CSRF": other_tokens[1][1],
            },
        )
        self.assertEqual(blocked.status_code, 409)

        released = self.client.post(
            "/api/v1/review/videos/video-1/lease/release",
            headers=self._mutation_headers(),
            json={"lease_token": first["lease_token"]},
        )
        self.assertEqual(released.status_code, 200)
        self.assertTrue(released.json()["released"])
        allowed = third_client.post(
            "/api/v1/review/videos/video-1/lease",
            headers={
                "Origin": "https://review.example.test",
                "X-Review-CSRF": other_tokens[1][1],
            },
        )
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
