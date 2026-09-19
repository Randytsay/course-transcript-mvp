from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.jobs.source import (
    SourceMetadata,
    _clear_directory_cache,
    inspect_rclone_selection,
    list_rclone_directory,
)


class DriveBrowsePerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_directory_cache()

    def tearDown(self) -> None:
        _clear_directory_cache()

    @patch("app.jobs.source._run_lsjson")
    def test_repeated_folder_browse_uses_short_cache(self, run_lsjson: Mock) -> None:
        run_lsjson.return_value = [
            {
                "Path": "課程",
                "Name": "課程",
                "IsDir": True,
            },
            {
                "Path": "介紹.mp3",
                "Name": "介紹.mp3",
                "Size": 1024,
                "ModTime": "2026-08-01T00:00:00Z",
            },
        ]

        with patch.dict(
            "os.environ",
            {"COURSE_TRANSCRIPT_DRIVE_BROWSE_CACHE_SECONDS": "60"},
            clear=False,
        ):
            first = list_rclone_directory("gdrive:")
            second = list_rclone_directory("gdrive:")

        self.assertEqual(first, second)
        self.assertEqual(run_lsjson.call_count, 1)

    @patch("app.jobs.source.inspect_rclone_source")
    @patch("app.jobs.source._run_lsjson")
    def test_multi_file_preview_uses_authoritative_exact_stat_per_file(
        self,
        run_lsjson: Mock,
        inspect_source: Mock,
    ) -> None:
        inspect_source.side_effect = [
            SourceMetadata(
                source_path="gdrive:課程/第一堂.mp3",
                name="第一堂.mp3",
                size_bytes=100,
                modified_at="2026-08-01T00:00:00Z",
                mime_type=None,
            ),
            SourceMetadata(
                source_path="gdrive:課程/第二堂.m4a",
                name="第二堂.m4a",
                size_bytes=200,
                modified_at="2026-08-01T01:00:00Z",
                mime_type=None,
            ),
        ]

        items = inspect_rclone_selection(
            selection_mode="files",
            source_paths=[
                "gdrive:課程/第一堂.mp3",
                "gdrive:課程/第二堂.m4a",
            ],
        )

        self.assertEqual([item.size_bytes for item in items], [100, 200])
        run_lsjson.assert_not_called()
        self.assertEqual(inspect_source.call_count, 2)

    @patch("app.jobs.source.inspect_rclone_source")
    @patch("app.jobs.source._run_lsjson")
    def test_missing_cached_metadata_falls_back_to_exact_stat(
        self,
        run_lsjson: Mock,
        inspect_source: Mock,
    ) -> None:
        run_lsjson.return_value = []
        inspect_source.return_value = SourceMetadata(
            source_path="gdrive:課程/新增.mp3",
            name="新增.mp3",
            size_bytes=321,
            modified_at=None,
            mime_type=None,
        )

        items = inspect_rclone_selection(
            selection_mode="files",
            source_paths=["gdrive:課程/新增.mp3"],
        )

        self.assertEqual(items[0].size_bytes, 321)
        inspect_source.assert_called_once_with("gdrive:課程/新增.mp3")


if __name__ == "__main__":
    unittest.main()
