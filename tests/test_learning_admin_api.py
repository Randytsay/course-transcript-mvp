from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.learning.admin as learning_admin
from app.learning.source import LearningSourceStore
from app.review.baseline import ensure_import_baseline
from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewStore


class LearningAdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.original_data_dir = learning_admin.DATA_DIR
        learning_admin.DATA_DIR = self.data_dir
        learning_admin._store_cache = None
        learning_admin._source_cache = None
        self.addCleanup(self._restore)

        self.review = ReviewStore(self.data_dir / "course-transcript.db")
        self.admin = ReviewAdminStore(self.data_dir / "course-transcript.db")
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 講",
            duration_ms=20_000,
        )
        self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "彌勒大成佛經"},
            ],
        )
        self.baseline = ensure_import_baseline(
            self.admin,
            youtube_video_id="video-1",
            triggered_by="owner@example.test",
        )
        app = FastAPI()
        app.include_router(learning_admin.router)
        self.client = TestClient(app, base_url="http://testserver")

    def _restore(self) -> None:
        learning_admin.DATA_DIR = self.original_data_dir
        learning_admin._store_cache = None
        learning_admin._source_cache = None

    def test_overview_reports_latest_version_and_missing_formal_source(self) -> None:
        response = self.client.get("/api/v1/review-admin/learning/overview")
        self.assertEqual(response.status_code, 200)
        row = response.json()["videos"][0]
        self.assertEqual(row["youtube_video_id"], "video-1")
        self.assertEqual(row["latest_version_number"], 1)
        self.assertIsNone(row["learning_source_version_id"])
        self.assertIsNone(row["artifact_id"])

    def test_learning_source_requires_explicit_owner_confirmation(self) -> None:
        denied = self.client.post(
            "/api/v1/review-admin/learning/videos/video-1/approve-source",
            json={"confirm": False},
        )
        self.assertEqual(denied.status_code, 422)
        self.assertIsNone(LearningSourceStore(self.data_dir / "course-transcript.db").get("video-1"))

        approved = self.client.post(
            "/api/v1/review-admin/learning/videos/video-1/approve-source",
            json={"confirm": True},
        )
        self.assertEqual(approved.status_code, 200)
        source = approved.json()["source"]
        self.assertEqual(source["subtitle_version_id"], self.baseline["id"])
        self.assertEqual(source["source_sha256"], self.baseline["content_sha256"])
        self.assertEqual(source["approved_by_actor"], "local-development")

    def test_paid_generation_requires_confirmation_and_formal_source(self) -> None:
        denied = self.client.post(
            "/api/v1/review-admin/learning/videos/video-1/generate",
            json={"confirm": False, "force": False},
        )
        self.assertEqual(denied.status_code, 422)

        without_source = self.client.post(
            "/api/v1/review-admin/learning/videos/video-1/generate",
            json={"confirm": True, "force": False},
        )
        self.assertEqual(without_source.status_code, 404)

        self.client.post(
            "/api/v1/review-admin/learning/videos/video-1/approve-source",
            json={"confirm": True},
        )
        fake_artifact = {
            "id": "artifact-1",
            "youtube_video_id": "video-1",
            "subtitle_version_id": self.baseline["id"],
            "source_sha256": self.baseline["content_sha256"],
        }
        with patch.object(
            learning_admin,
            "generate_study_pack",
            return_value={"artifact": fake_artifact, "generated": True},
        ) as generate:
            response = self.client.post(
                "/api/v1/review-admin/learning/videos/video-1/generate",
                json={"confirm": True, "force": False},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["generated"])
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["youtube_video_id"], "video-1")
        self.assertEqual(generate.call_args.kwargs["actor"], "local-development")

    def test_cloudflare_access_and_origin_are_enforced_for_owner_mutation(self) -> None:
        with patch.dict(
            os.environ,
            {
                "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "true",
                "COURSE_TRANSCRIPT_PUBLIC_ORIGIN": "https://transcript.example.test",
            },
            clear=False,
        ):
            no_access = self.client.post(
                "/api/v1/review-admin/learning/videos/video-1/approve-source",
                json={"confirm": True},
                headers={"Origin": "https://transcript.example.test"},
            )
            self.assertEqual(no_access.status_code, 401)

            wrong_origin = self.client.post(
                "/api/v1/review-admin/learning/videos/video-1/approve-source",
                json={"confirm": True},
                headers={
                    "Origin": "https://evil.example.test",
                    "Cf-Access-Authenticated-User-Email": "owner@example.test",
                    "Cf-Access-Jwt-Assertion": "test-assertion",
                },
            )
            self.assertEqual(wrong_origin.status_code, 403)

            approved = self.client.post(
                "/api/v1/review-admin/learning/videos/video-1/approve-source",
                json={"confirm": True},
                headers={
                    "Origin": "https://transcript.example.test",
                    "Cf-Access-Authenticated-User-Email": "owner@example.test",
                    "Cf-Access-Jwt-Assertion": "test-assertion",
                },
            )
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["source"]["approved_by_actor"], "owner@example.test")


if __name__ == "__main__":
    unittest.main()
