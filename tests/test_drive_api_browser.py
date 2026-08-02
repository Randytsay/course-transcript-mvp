from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.jobs.drive_api import (
    DriveApiError,
    drive_health,
    list_drive_directory,
    reset_drive_api_state,
    search_drive,
)


class DriveApiBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_drive_api_state()

    def tearDown(self) -> None:
        reset_drive_api_state()

    def test_lists_root_with_rclone_compatible_paths_and_page_token(self) -> None:
        payload = {
            "nextPageToken": "next-1",
            "files": [
                {
                    "id": "folder-1",
                    "name": "課程 A",
                    "mimeType": "application/vnd.google-apps.folder",
                    "modifiedTime": "2026-08-03T00:00:00Z",
                },
                {
                    "id": "audio-1",
                    "name": "第一課.m4a",
                    "mimeType": "audio/mp4",
                    "size": "1024",
                    "modifiedTime": "2026-08-03T00:00:00Z",
                },
            ],
        }
        with patch("app.jobs.drive_api._api_json", return_value=payload) as api:
            page = list_drive_directory("gdrive:", page_size=50)

        self.assertEqual(page.current_path, "gdrive:")
        self.assertIsNone(page.parent_path)
        self.assertEqual(page.next_page_token, "next-1")
        self.assertEqual([item.name for item in page.entries], ["課程 A", "第一課.m4a"])
        self.assertEqual(page.entries[0].source_path, "gdrive:課程 A")
        self.assertTrue(page.entries[0].is_dir)
        self.assertTrue(page.entries[1].supported_media)
        params = api.call_args.args[1]
        self.assertEqual(params["pageSize"], 50)
        self.assertEqual(params["includeItemsFromAllDrives"], "true")

    def test_resolves_child_folder_then_lists_children(self) -> None:
        responses = [
            {"files": [{"id": "folder-1", "name": "課程 A"}]},
            {
                "files": [
                    {
                        "id": "audio-2",
                        "name": "第二課.mp3",
                        "mimeType": "audio/mpeg",
                        "size": "2048",
                    }
                ]
            },
        ]
        with patch("app.jobs.drive_api._api_json", side_effect=responses) as api:
            page = list_drive_directory("gdrive:課程 A")

        self.assertEqual(page.parent_path, "gdrive:")
        self.assertEqual(page.entries[0].source_path, "gdrive:課程 A/第二課.mp3")
        self.assertIn("name = '課程 A'", api.call_args_list[0].args[1]["q"])
        self.assertIn("'folder-1' in parents", api.call_args_list[1].args[1]["q"])

    def test_search_requires_two_characters(self) -> None:
        with self.assertRaisesRegex(DriveApiError, "至少輸入 2 個字元"):
            search_drive("a")

    def test_search_returns_supported_media(self) -> None:
        with patch(
            "app.jobs.drive_api._api_json",
            return_value={
                "files": [
                    {
                        "id": "video-1",
                        "name": "佛經課程.mp4",
                        "mimeType": "video/mp4",
                        "size": "4096",
                    }
                ]
            },
        ) as api:
            page = search_drive("佛經")

        self.assertEqual(page.entries[0].name, "佛經課程.mp4")
        self.assertTrue(page.entries[0].supported_media)
        self.assertIn("name contains '佛經'", api.call_args.args[1]["q"])

    def test_health_does_not_expose_identity(self) -> None:
        with patch(
            "app.jobs.drive_api._api_json",
            return_value={
                "user": {"displayName": "Private User"},
                "storageQuota": {"usage": "1"},
            },
        ):
            result = drive_health()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["account_available"])
        self.assertNotIn("displayName", result)
        self.assertFalse(result["paid_operation_started"])

    def test_rejects_non_drive_path(self) -> None:
        with self.assertRaisesRegex(DriveApiError, "不在允許"):
            list_drive_directory("other:folder")


if __name__ == "__main__":
    unittest.main()
