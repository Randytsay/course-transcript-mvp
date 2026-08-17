from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.review.store import ReviewConflict, ReviewStore, changed_char_count


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self._temporary.name) / "course-transcript.db")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def create_user(self, *, subject: str = "google-1", name: str = "法專師姐"):
        return self.store.get_or_create_user_for_identity(
            provider="google",
            provider_subject=subject,
            display_name=name,
            email=f"{subject}@example.test",
        )

    def create_video(self, *, video_id: str = "yt-video-1"):
        self.store.upsert_video(
            youtube_video_id=video_id,
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 8 集",
            duration_ms=3_600_000,
            caption_track_id="caption-1",
        )
        return self.store.import_subtitle_segments(
            youtube_video_id=video_id,
            segments=[
                {
                    "segment_index": 1,
                    "start_ms": 0,
                    "end_ms": 5_000,
                    "text": "佛告阿難",
                },
                {
                    "segment_index": 2,
                    "start_ms": 5_000,
                    "end_ms": 10_000,
                    "text": "彌勒大成佛今",
                },
            ],
        )

    def test_changed_char_count_is_human_oriented(self):
        self.assertEqual(changed_char_count("彌勒大成佛今", "彌勒大成佛經"), 1)
        self.assertEqual(changed_char_count("佛告阿難", "佛告阿難尊者"), 2)
        self.assertEqual(changed_char_count("甲乙丙", "甲丙"), 1)

    def test_first_login_is_auto_active_and_identity_is_stable(self):
        first = self.create_user()
        second = self.store.get_or_create_user_for_identity(
            provider="google",
            provider_subject="google-1",
            display_name="法專師姐（更新名稱）",
        )

        self.assertEqual(first["status"], "active")
        self.assertEqual(first["role"], "reviewer")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["display_name"], "法專師姐（更新名稱）")

    def test_google_and_line_can_link_to_same_logical_user(self):
        user = self.create_user()

        linked = self.store.link_identity(
            user_id=user["id"],
            provider="line",
            provider_subject="U-line-123",
        )
        via_line = self.store.get_or_create_user_for_identity(
            provider="line",
            provider_subject="U-line-123",
            display_name="法專師姐",
        )

        self.assertEqual(via_line["id"], user["id"])
        self.assertEqual(
            {item["provider"] for item in linked["identities"]},
            {"google", "line"},
        )

    def test_identity_cannot_be_silently_merged_into_another_user(self):
        first = self.create_user(subject="google-a", name="甲師兄")
        second = self.create_user(subject="google-b", name="乙師兄")
        self.store.link_identity(
            user_id=first["id"],
            provider="line",
            provider_subject="U-shared",
        )

        with self.assertRaises(ReviewConflict):
            self.store.link_identity(
                user_id=second["id"],
                provider="line",
                provider_subject="U-shared",
            )

    def test_initial_subtitle_import_never_overwrites_existing_review_state(self):
        self.create_video()

        with self.assertRaises(ReviewConflict):
            self.store.import_subtitle_segments(
                youtube_video_id="yt-video-1",
                segments=[
                    {
                        "segment_index": 1,
                        "start_ms": 0,
                        "end_ms": 5_000,
                        "text": "另一份字幕",
                    }
                ],
            )

    def test_submission_counts_immediately_without_mutating_working_subtitle(self):
        user = self.create_user()
        segments = self.create_video()

        suggestion = self.store.submit_suggestion(
            segment_id=segments[1]["id"],
            user_id=user["id"],
            suggested_text="彌勒大成佛經",
        )
        leaderboard = self.store.contribution_leaderboard()
        current = self.store.list_segments("yt-video-1")

        self.assertEqual(suggestion["status"], "pending")
        self.assertEqual(suggestion["changed_chars"], 1)
        self.assertEqual(current[1]["working_text"], "彌勒大成佛今")
        self.assertEqual(leaderboard[0]["suggestions_sent"], 1)
        self.assertEqual(leaderboard[0]["changed_chars"], 1)
        self.assertEqual(leaderboard[0]["videos_contributed"], 1)

    def test_revising_pending_suggestion_does_not_inflate_submission_count(self):
        user = self.create_user()
        segments = self.create_video()
        suggestion = self.store.submit_suggestion(
            segment_id=segments[1]["id"],
            user_id=user["id"],
            suggested_text="彌勒大成佛經",
        )

        revised = self.store.revise_suggestion(
            suggestion_id=suggestion["id"],
            user_id=user["id"],
            suggested_text="彌勒大成佛經。",
        )
        leaderboard = self.store.contribution_leaderboard()

        self.assertEqual(revised["suggested_text"], "彌勒大成佛經。")
        self.assertEqual(leaderboard[0]["suggestions_sent"], 1)
        self.assertEqual(leaderboard[0]["changed_chars"], 2)

    def test_progress_keeps_reviewed_boundary_but_allows_playback_to_move(self):
        user = self.create_user()
        self.create_video()

        self.store.update_progress(
            user_id=user["id"],
            youtube_video_id="yt-video-1",
            last_playback_ms=20_000,
            reviewed_until_ms=15_000,
            last_segment_index=2,
        )
        progress = self.store.update_progress(
            user_id=user["id"],
            youtube_video_id="yt-video-1",
            last_playback_ms=8_000,
            reviewed_until_ms=10_000,
            last_segment_index=1,
        )
        resume = self.store.get_resume_point(user["id"])

        self.assertEqual(progress["last_playback_ms"], 8_000)
        self.assertEqual(progress["reviewed_until_ms"], 15_000)
        self.assertIsNotNone(resume)
        assert resume is not None
        self.assertEqual(resume["youtube_video_id"], "yt-video-1")
        self.assertEqual(resume["last_playback_ms"], 8_000)

    def test_contribution_detail_lists_videos_and_completed_review(self):
        user = self.create_user()
        segments = self.create_video()
        self.store.submit_suggestion(
            segment_id=segments[0]["id"],
            user_id=user["id"],
            suggested_text="佛告阿難尊者",
        )
        self.store.update_progress(
            user_id=user["id"],
            youtube_video_id="yt-video-1",
            last_playback_ms=3_600_000,
            reviewed_until_ms=3_600_000,
            completed=True,
        )

        detail = self.store.user_contribution_detail(user["id"])

        self.assertEqual(detail["suggestions_sent"], 1)
        self.assertEqual(detail["changed_chars"], 2)
        self.assertEqual(detail["videos_contributed"], 1)
        self.assertEqual(detail["completed_videos"], 1)
        self.assertEqual(detail["videos"][0]["title"], "彌勒大成佛經 第 8 集")


if __name__ == "__main__":
    unittest.main()
