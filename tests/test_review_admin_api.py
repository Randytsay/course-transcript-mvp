from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.review.admin as admin_api
from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewStore


class ReviewAdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.original_data_dir = admin_api.DATA_DIR
        admin_api.DATA_DIR = self.data_dir
        admin_api._store_cache = None
        self.addCleanup(self._restore)

        self.review = ReviewStore(self.data_dir / "course-transcript.db")
        self.admin = ReviewAdminStore(self.data_dir / "course-transcript.db")
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 集",
            duration_ms=10000,
            caption_track_id="caption-1",
        )
        segments = self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5000, "end_ms": 10000, "text": "彌勒大成佛今"},
            ],
        )
        user = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="reviewer-1",
            display_name="法專師姐",
        )
        self.suggestion = self.review.submit_suggestion(
            segment_id=segments[1]["id"],
            user_id=user["id"],
            suggested_text="彌勒大成佛經",
        )
        app = FastAPI()
        app.include_router(admin_api.router)
        self.client = TestClient(app, base_url="http://testserver")

    def _restore(self) -> None:
        admin_api.DATA_DIR = self.original_data_dir
        admin_api._store_cache = None

    def test_approve_requires_confirmation_and_returns_compact_version(self) -> None:
        denied = self.client.post(
            f"/api/v1/review-admin/suggestions/{self.suggestion['id']}/approve",
            json={"confirm": False},
        )
        self.assertEqual(denied.status_code, 422)

        approved = self.client.post(
            f"/api/v1/review-admin/suggestions/{self.suggestion['id']}/approve",
            json={"confirm": True},
        )
        self.assertEqual(approved.status_code, 200)
        body = approved.json()
        self.assertEqual(body["suggestion"]["status"], "approved")
        self.assertEqual(body["version"]["version_number"], 1)
        self.assertNotIn("srt_text", body["version"])
        self.assertNotIn("snapshot_json", body["version"])

        rows = self.client.get("/api/v1/review-admin/suggestions?status=approved").json()
        reviewed = rows["suggestions"][0]
        self.assertEqual(reviewed["reviewed_by_actor"], "local-development")
        self.assertEqual(reviewed["review_action"], "suggestion_approved")

    def test_versions_list_is_compact_but_detail_contains_immutable_srt(self) -> None:
        approved = self.client.post(
            f"/api/v1/review-admin/suggestions/{self.suggestion['id']}/approve",
            json={"confirm": True},
        ).json()
        version_id = approved["version"]["id"]
        listing = self.client.get("/api/v1/review-admin/versions")
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn("srt_text", listing.json()["versions"][0])
        detail = self.client.get(f"/api/v1/review-admin/versions/{version_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("彌勒大成佛經", detail.json()["version"]["srt_text"])

    def test_publish_requires_confirmation_and_is_idempotent_after_success(self) -> None:
        approved = self.client.post(
            f"/api/v1/review-admin/suggestions/{self.suggestion['id']}/approve",
            json={"confirm": True},
        ).json()
        version_id = approved["version"]["id"]

        denied = self.client.post(
            f"/api/v1/review-admin/versions/{version_id}/publish",
            json={"confirm": False},
        )
        self.assertEqual(denied.status_code, 422)

        with patch.object(
            admin_api,
            "publish_caption_version",
            return_value={"id": "caption-1", "snippet": {"lastUpdated": "now"}},
        ) as publish:
            first = self.client.post(
                f"/api/v1/review-admin/versions/{version_id}/publish",
                json={"confirm": True},
            )
            second = self.client.post(
                f"/api/v1/review-admin/versions/{version_id}/publish",
                json={"confirm": True},
            )

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["already_published"])
        self.assertEqual(first.json()["version"]["publish_status"], "published")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["already_published"])
        publish.assert_called_once()
        self.assertIn("彌勒大成佛經", publish.call_args.kwargs["srt_text"])

    def test_publish_failure_is_persisted_without_claiming_success(self) -> None:
        approved = self.client.post(
            f"/api/v1/review-admin/suggestions/{self.suggestion['id']}/approve",
            json={"confirm": True},
        ).json()
        version_id = approved["version"]["id"]
        with patch.object(
            admin_api,
            "publish_caption_version",
            side_effect=admin_api.YouTubePublishError("quota or authorization failure"),
        ):
            response = self.client.post(
                f"/api/v1/review-admin/versions/{version_id}/publish",
                json={"confirm": True},
            )
        self.assertEqual(response.status_code, 502)
        stored = self.admin.get_version(version_id)
        self.assertEqual(stored["publish_status"], "publish_failed")
        self.assertIn("authorization failure", stored["publish_error"])


if __name__ == "__main__":
    unittest.main()
