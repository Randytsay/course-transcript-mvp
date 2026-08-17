from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewConflict, ReviewStore


class ReviewAdminStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        self.review = ReviewStore(self.database)
        self.admin = ReviewAdminStore(self.database)
        self.review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 集",
            duration_ms=10000,
            caption_track_id="caption-1",
        )
        self.review.upsert_video(
            youtube_video_id="video-2",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 2 集",
            duration_ms=10000,
            caption_track_id="caption-2",
        )
        self.video1_segments = self.review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5000, "end_ms": 10000, "text": "彌勒大成佛今"},
            ],
        )
        self.video2_segments = self.review.import_subtitle_segments(
            youtube_video_id="video-2",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5000, "text": "今日讀彌勒大成佛今"},
                {"segment_index": 2, "start_ms": 5000, "end_ms": 10000, "text": "戴師兄開示"},
            ],
        )
        self.reviewer1 = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="reviewer-1",
            display_name="法專師姐",
        )
        self.reviewer2 = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="reviewer-2",
            display_name="法明師兄",
        )

    def test_approve_suggestion_changes_working_text_and_freezes_version(self) -> None:
        suggestion = self.review.submit_suggestion(
            segment_id=self.video1_segments[1]["id"],
            user_id=self.reviewer1["id"],
            suggested_text="彌勒大成佛經",
        )
        result = self.admin.approve_suggestion(
            suggestion_id=suggestion["id"],
            actor="owner@example.test",
        )
        self.assertEqual(result["suggestion"]["status"], "approved")
        segment = self.review.list_segments("video-1")[1]
        self.assertEqual(segment["working_text"], "彌勒大成佛經")
        self.assertEqual(segment["revision"], 1)
        version = result["version"]
        self.assertEqual(version["version_number"], 1)
        self.assertEqual(version["source"], "suggestion_approval")
        self.assertIn("彌勒大成佛經", version["srt_text"])
        snapshot = json.loads(version["snapshot_json"])
        self.assertEqual(snapshot[1]["revision"], 1)

    def test_second_suggestion_on_old_revision_becomes_conflict(self) -> None:
        first = self.review.submit_suggestion(
            segment_id=self.video1_segments[1]["id"],
            user_id=self.reviewer1["id"],
            suggested_text="彌勒大成佛經",
        )
        second = self.review.submit_suggestion(
            segment_id=self.video1_segments[1]["id"],
            user_id=self.reviewer2["id"],
            suggested_text="彌勒大成佛經。",
        )
        self.admin.approve_suggestion(suggestion_id=first["id"], actor="owner@example.test")
        rows = self.admin.list_suggestions(status="pending")
        remaining = next(item for item in rows if item["id"] == second["id"])
        self.assertTrue(remaining["conflict"])
        with self.assertRaises(ReviewConflict):
            self.admin.approve_suggestion(suggestion_id=second["id"], actor="owner@example.test")
        second_after = next(
            item for item in self.admin.list_suggestions(status="pending") if item["id"] == second["id"]
        )
        self.assertEqual(second_after["status"], "pending")
        self.assertEqual(self.review.list_segments("video-1")[1]["working_text"], "彌勒大成佛經")

    def test_reject_does_not_change_subtitle_text(self) -> None:
        suggestion = self.review.submit_suggestion(
            segment_id=self.video1_segments[0]["id"],
            user_id=self.reviewer1["id"],
            suggested_text="佛告阿難尊者",
        )
        rejected = self.admin.reject_suggestion(
            suggestion_id=suggestion["id"],
            actor="owner@example.test",
            reason="維持經文原句",
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.review.list_segments("video-1")[0]["working_text"], "佛告阿難")
        with self.admin.connect() as connection:
            audit = connection.execute(
                """
                SELECT * FROM review_admin_audit
                WHERE entity_type = 'suggestion' AND entity_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (suggestion["id"],),
            ).fetchone()
        self.assertEqual(audit["actor"], "owner@example.test")
        self.assertEqual(audit["action"], "suggestion_rejected")
        self.assertIn("維持經文原句", audit["payload_json"])

    def test_batch_preview_apply_selected_and_create_one_version_per_video(self) -> None:
        batch = self.admin.create_batch(
            find_text="彌勒大成佛今",
            replace_text="彌勒大成佛經",
            actor="owner@example.test",
        )
        self.assertEqual(batch["batch"]["status"], "draft")
        self.assertEqual(len(batch["items"]), 2)
        first_id = batch["items"][0]["id"]
        result = self.admin.apply_batch(
            batch_id=batch["batch"]["id"],
            actor="owner@example.test",
            item_ids=[first_id],
        )
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["conflict_count"], 0)
        self.assertEqual(len(result["versions"]), 1)
        statuses = {item["id"]: item["status"] for item in result["batch"]["items"]}
        self.assertEqual(statuses[first_id], "applied")
        self.assertIn("skipped", statuses.values())

    def test_batch_rechecks_revision_and_excludes_conflicted_item(self) -> None:
        batch = self.admin.create_batch(
            find_text="彌勒大成佛今",
            replace_text="彌勒大成佛經",
            actor="owner@example.test",
        )
        target = next(item for item in batch["items"] if item["youtube_video_id"] == "video-1")
        with self.review.transaction() as connection:
            connection.execute(
                """
                UPDATE review_subtitle_segments
                SET working_text = '人工先改過', revision = revision + 1
                WHERE id = ?
                """,
                (target["segment_id"],),
            )
        result = self.admin.apply_batch(
            batch_id=batch["batch"]["id"],
            actor="owner@example.test",
        )
        self.assertEqual(result["conflict_count"], 1)
        conflicted = next(item for item in result["batch"]["items"] if item["id"] == target["id"])
        self.assertEqual(conflicted["status"], "conflict")
        self.assertEqual(conflicted["current_text"], "人工先改過")
        self.assertEqual(result["applied_count"], 1)

    def test_restore_version_creates_new_version_without_changing_timing(self) -> None:
        first = self.review.submit_suggestion(
            segment_id=self.video1_segments[1]["id"],
            user_id=self.reviewer1["id"],
            suggested_text="彌勒大成佛經",
        )
        version1 = self.admin.approve_suggestion(
            suggestion_id=first["id"], actor="owner@example.test"
        )["version"]
        current_segment = self.review.list_segments("video-1")[1]
        second = self.review.submit_suggestion(
            segment_id=current_segment["id"],
            user_id=self.reviewer2["id"],
            suggested_text="彌勒大成佛經。",
        )
        version2 = self.admin.approve_suggestion(
            suggestion_id=second["id"], actor="owner@example.test"
        )["version"]
        self.assertEqual(version2["version_number"], 2)

        restored = self.admin.restore_version(
            version_id=version1["id"], actor="owner@example.test"
        )
        self.assertEqual(restored["version"]["version_number"], 3)
        segment = self.review.list_segments("video-1")[1]
        self.assertEqual(segment["working_text"], "彌勒大成佛經")
        self.assertEqual(segment["start_ms"], 5000)
        self.assertEqual(segment["end_ms"], 10000)


if __name__ == "__main__":
    unittest.main()
