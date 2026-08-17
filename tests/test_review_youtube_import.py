from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.review.youtube_import as youtube_import
from app.review.store import ReviewStore


SRT = """1
00:00:00,000 --> 00:00:03,000
佛告阿難

2
00:00:03,000 --> 00:00:07,500
彌勒大成佛經
"""


class YouTubeReviewImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.original_data_dir = youtube_import.DATA_DIR
        youtube_import.DATA_DIR = Path(temporary.name)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        youtube_import.DATA_DIR = self.original_data_dir

    @staticmethod
    def _playlist():
        return [
            {"youtube_video_id": "video-1", "title": "彌勒大成佛經 第 1 集"},
            {"youtube_video_id": "video-2", "title": "彌勒大成佛經 第 2 集"},
        ]

    @staticmethod
    def _caption(video_id: str):
        return {
            "id": f"caption-{video_id}",
            "snippet": {
                "videoId": video_id,
                "language": "zh-TW",
                "name": "繁體中文",
                "trackKind": "standard",
                "isDraft": False,
                "status": "serving",
            },
        }

    def test_preview_selects_tracks_without_downloading_or_mutating(self) -> None:
        with (
            patch.object(youtube_import, "_playlist_items", return_value=self._playlist()),
            patch.object(youtube_import, "_select_caption", side_effect=lambda video_id, **_: self._caption(video_id)),
            patch.object(youtube_import, "_download_srt") as download,
        ):
            result = youtube_import.sync_playlist(
                playlist_id="playlist-1",
                access_token="token",
                max_videos=50,
                apply=False,
                actor="owner@example.test",
            )

        self.assertEqual(result["playlist_items"], 2)
        self.assertEqual([item["status"] for item in result["results"]], ["ready", "ready"])
        download.assert_not_called()
        store = ReviewStore(youtube_import.DATA_DIR / "course-transcript.db")
        with store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_videos").fetchone()[0], 0)

    def test_apply_imports_fixed_srt_segments_and_metadata(self) -> None:
        with (
            patch.object(youtube_import, "_playlist_items", return_value=self._playlist()[:1]),
            patch.object(youtube_import, "_select_caption", return_value=self._caption("video-1")),
            patch.object(youtube_import, "_download_srt", return_value=SRT),
        ):
            result = youtube_import.sync_playlist(
                playlist_id="playlist-1",
                access_token="token",
                max_videos=50,
                apply=True,
                actor="owner@example.test",
            )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["results"][0]["segment_count"], 2)
        self.assertEqual(result["results"][0]["duration_ms"], 7500)

        store = ReviewStore(youtube_import.DATA_DIR / "course-transcript.db")
        segments = store.list_segments("video-1")
        self.assertEqual([item["start_ms"] for item in segments], [0, 3000])
        self.assertEqual([item["working_text"] for item in segments], ["佛告阿難", "彌勒大成佛經"])
        with store.connect() as connection:
            video = connection.execute(
                "SELECT * FROM review_videos WHERE youtube_video_id = 'video-1'"
            ).fetchone()
        self.assertEqual(video["playlist_id"], "playlist-1")
        self.assertEqual(video["caption_track_id"], "caption-video-1")
        self.assertEqual(video["caption_language"], "zh-TW")

    def test_rerun_skips_existing_segments_before_caption_quota_calls(self) -> None:
        store = ReviewStore(youtube_import.DATA_DIR / "course-transcript.db")
        store.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 集",
            duration_ms=7500,
            caption_track_id="caption-video-1",
        )
        store.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 3000, "text": "佛告阿難"},
            ],
        )
        with (
            patch.object(youtube_import, "_playlist_items", return_value=self._playlist()[:1]),
            patch.object(youtube_import, "_select_caption") as select_caption,
            patch.object(youtube_import, "_download_srt") as download,
        ):
            result = youtube_import.sync_playlist(
                playlist_id="playlist-1",
                access_token="token",
                max_videos=50,
                apply=True,
                actor="owner@example.test",
            )

        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["imported"], 0)
        select_caption.assert_not_called()
        download.assert_not_called()

    def test_caption_selection_prefers_configured_standard_language(self) -> None:
        payload = {
            "items": [
                {
                    "id": "asr-zh",
                    "snippet": {
                        "language": "zh-TW",
                        "trackKind": "ASR",
                        "isDraft": False,
                        "status": "serving",
                    },
                },
                {
                    "id": "manual-en",
                    "snippet": {
                        "language": "en",
                        "trackKind": "standard",
                        "isDraft": False,
                        "status": "serving",
                    },
                },
                {
                    "id": "manual-zh",
                    "snippet": {
                        "language": "zh-TW",
                        "trackKind": "standard",
                        "isDraft": False,
                        "status": "serving",
                    },
                },
            ]
        }
        with (
            patch.dict(os.environ, {"YOUTUBE_REVIEW_CAPTION_LANGUAGES": "zh-TW,zh-Hant,zh"}),
            patch.object(youtube_import, "_get_json", return_value=payload),
        ):
            selected = youtube_import._select_caption("video-1", access_token="token")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["id"], "manual-zh")

    def test_invalid_srt_fails_one_video_without_partial_rows(self) -> None:
        invalid = "1\n00:00:03,000 --> 00:00:02,000\n錯誤時間碼\n"
        with (
            patch.object(youtube_import, "_playlist_items", return_value=self._playlist()[:1]),
            patch.object(youtube_import, "_select_caption", return_value=self._caption("video-1")),
            patch.object(youtube_import, "_download_srt", return_value=invalid),
        ):
            result = youtube_import.sync_playlist(
                playlist_id="playlist-1",
                access_token="token",
                max_videos=50,
                apply=True,
                actor="owner@example.test",
            )
        self.assertEqual(result["failed"], 1)
        store = ReviewStore(youtube_import.DATA_DIR / "course-transcript.db")
        with store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_videos").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_subtitle_segments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
