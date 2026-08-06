from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.drive_api_routes import _read_actor, _rclone_parent
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

    def test_search_resolves_nested_file_to_real_rclone_path(self) -> None:
        responses = [
            {
                "files": [
                    {
                        "id": "video-1",
                        "name": "佛經課程.mp4",
                        "mimeType": "video/mp4",
                        "size": "4096",
                        "parents": ["folder-2"],
                    }
                ]
            },
            {
                "id": "folder-2",
                "name": "第二階段",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["folder-1"],
            },
            {
                "id": "folder-1",
                "name": "課程 A",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["root"],
            },
        ]
        with patch("app.jobs.drive_api._api_json", side_effect=responses):
            page = search_drive("佛經")

        self.assertEqual(page.entries[0].source_path, "gdrive:課程 A/第二階段/佛經課程.mp4")
        self.assertTrue(page.entries[0].supported_media)

    def test_search_folder_can_be_opened_from_cached_path(self) -> None:
        responses = [
            {
                "files": [
                    {
                        "id": "folder-1",
                        "name": "課程 A",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": ["root"],
                    }
                ]
            },
            {"files": []},
        ]
        with patch("app.jobs.drive_api._api_json", side_effect=responses):
            search_page = search_drive("課程")
            browse_page = list_drive_directory(search_page.entries[0].source_path)
        self.assertEqual(browse_page.current_path, "gdrive:課程 A")

    def test_duplicate_names_in_same_folder_are_rejected(self) -> None:
        with patch(
            "app.jobs.drive_api._api_json",
            return_value={
                "files": [
                    {"id": "one", "name": "同名", "mimeType": "application/vnd.google-apps.folder"},
                    {"id": "two", "name": "同名", "mimeType": "application/vnd.google-apps.folder"},
                ]
            },
        ):
            with self.assertRaisesRegex(DriveApiError, "重複名稱"):
                list_drive_directory("gdrive:")

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

    def test_read_actor_does_not_require_origin_for_get(self) -> None:
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/api/v1/drive/health",
            "headers": [
                (b"cf-access-authenticated-user-email", b"user@example.com"),
                (b"cf-access-jwt-assertion", b"assertion"),
            ],
        })
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "true"}):
            self.assertEqual(_read_actor(request), "user@example.com")

    def test_read_actor_requires_cloudflare_identity_when_enabled(self) -> None:
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "true"}):
            with self.assertRaises(HTTPException) as raised:
                _read_actor(request)
        self.assertEqual(raised.exception.status_code, 401)

    def test_rclone_parent_navigation(self) -> None:
        self.assertIsNone(_rclone_parent("gdrive:"))
        self.assertEqual(_rclone_parent("gdrive:課程 A"), "gdrive:")
        self.assertEqual(_rclone_parent("gdrive:課程 A/第二階段"), "gdrive:課程 A")

    def test_rejects_non_drive_path(self) -> None:
        with self.assertRaisesRegex(DriveApiError, "不在允許"):
            list_drive_directory("other:folder")


if __name__ == "__main__":
    unittest.main()
