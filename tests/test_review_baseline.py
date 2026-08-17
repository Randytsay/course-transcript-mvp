from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.review.admin_store import ReviewAdminStore
from app.review.baseline import ensure_batch_baselines, ensure_import_baseline
from app.review.store import ReviewStore


class ReviewImportBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        self.review = ReviewStore(self.database)
        self.admin = ReviewAdminStore(self.database)
        for video_id, title, text in (
            ("video-1", "第 1 集", "彌勒大成佛今"),
            ("video-2", "第 2 集", "今日讀彌勒大成佛今"),
        ):
            self.review.upsert_video(
                youtube_video_id=video_id,
                playlist_id="playlist-1",
                title=title,
                duration_ms=5000,
                caption_track_id=f"caption-{video_id}",
            )
            self.review.import_subtitle_segments(
                youtube_video_id=video_id,
                segments=[
                    {
                        "segment_index": 1,
                        "start_ms": 0,
                        "end_ms": 5000,
                        "text": text,
                    }
                ],
            )

    def test_baseline_is_idempotent_and_uses_immutable_original_text(self) -> None:
        with self.review.transaction() as connection:
            connection.execute(
                """
                UPDATE review_subtitle_segments
                SET working_text = '已經被改過', revision = 7
                WHERE youtube_video_id = 'video-1'
                """
            )
        first = ensure_import_baseline(
            self.admin,
            youtube_video_id="video-1",
            triggered_by="owner@example.test",
        )
        second = ensure_import_baseline(
            self.admin,
            youtube_video_id="video-1",
            triggered_by="owner@example.test",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["version_number"], 1)
        self.assertEqual(first["source"], "import_baseline")
        self.assertIn("彌勒大成佛今", first["srt_text"])
        self.assertNotIn("已經被改過", first["srt_text"])
        with self.admin.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM review_subtitle_versions WHERE youtube_video_id = 'video-1'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_batch_baseline_only_freezes_selected_video_occurrences(self) -> None:
        batch = self.admin.create_batch(
            find_text="彌勒大成佛今",
            replace_text="彌勒大成佛經",
            actor="owner@example.test",
        )
        selected = next(
            item for item in batch["items"] if item["youtube_video_id"] == "video-1"
        )
        baselines = ensure_batch_baselines(
            self.admin,
            batch_id=batch["batch"]["id"],
            item_ids=[selected["id"]],
            triggered_by="owner@example.test",
        )
        self.assertEqual(len(baselines), 1)
        self.assertEqual(baselines[0]["youtube_video_id"], "video-1")
        with self.admin.connect() as connection:
            rows = connection.execute(
                "SELECT youtube_video_id, source FROM review_subtitle_versions ORDER BY youtube_video_id"
            ).fetchall()
        self.assertEqual(
            [(row["youtube_video_id"], row["source"]) for row in rows],
            [("video-1", "import_baseline")],
        )


if __name__ == "__main__":
    unittest.main()
