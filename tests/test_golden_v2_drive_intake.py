from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.jobs.drive_api import DriveApiError, download_drive_media, reset_drive_api_state
from app.pipeline.golden_v2_drive_intake import (
    SourceIntakeBusyError,
    _INTAKE_LOCK,
    intake_drive_source,
)


class GoldenV2DriveDownloadTests(unittest.TestCase):
    def setUp(self):
        reset_drive_api_state()
        self.data = b"known audio bytes\x00\x01"
        self.md5 = hashlib.md5(self.data, usedforsecurity=False).hexdigest()
        self.item = {
            "id": "drive-file-1", "name": "lesson.mp3", "mimeType": "audio/mpeg",
            "size": str(len(self.data)), "version": "7", "md5Checksum": self.md5,
            "trashed": False, "parents": ["root"],
            "modifiedTime": "2026-10-12T00:00:00Z",
        }

    def tearDown(self):
        reset_drive_api_state()

    def _api_responses(self, after=None, listing=None):
        return [
            listing if listing is not None else {"files": [{"id": "drive-file-1", "name": "lesson.mp3"}]},
            self.item,
            after if after is not None else self.item,
        ]

    def test_download_streams_bytes_and_binds_stable_drive_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "selected-source"
            response = io.BytesIO(self.data)
            with patch("app.jobs.drive_api._api_json", side_effect=self._api_responses()) as api, \
                    patch("app.jobs.drive_api._refresh_access_token", return_value="test-token"), \
                    patch("app.jobs.drive_api.urllib.request.urlopen", return_value=response) as urlopen:
                observation = download_drive_media("gdrive:lesson.mp3", target)
            self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(observation["provider"], "google_drive")
        self.assertEqual(observation["file_id"], "drive-file-1")
        self.assertEqual(observation["version"], "7")
        self.assertEqual(observation["downloaded_sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(observation["downloaded_md5"], self.md5)
        self.assertEqual(api.call_count, 3)
        self.assertIn("version", api.call_args_list[1].args[1]["fields"])
        self.assertIn("alt=media", urlopen.call_args.args[0].full_url)

    def test_version_change_after_download_discards_local_copy(self):
        changed = {**self.item, "version": "8"}
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "selected-source"
            with patch("app.jobs.drive_api._api_json", side_effect=self._api_responses(after=changed)), \
                    patch("app.jobs.drive_api._refresh_access_token", return_value="test-token"), \
                    patch("app.jobs.drive_api.urllib.request.urlopen", return_value=io.BytesIO(self.data)):
                with self.assertRaisesRegex(DriveApiError, "前後不一致"):
                    download_drive_media("gdrive:lesson.mp3", target)
            self.assertFalse(target.exists())

    def test_duplicate_path_and_oversized_source_fail_before_download(self):
        with patch("app.jobs.drive_api._api_json", side_effect=self._api_responses(
            listing={"files": [{"id": "a"}, {"id": "b"}]},
        )):
            with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(DriveApiError, "無法唯一"):
                download_drive_media("gdrive:lesson.mp3", Path(temp) / "source")

        with patch("app.jobs.drive_api._api_json", side_effect=self._api_responses()), \
                patch("app.jobs.drive_api.urllib.request.urlopen") as urlopen:
            with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(DriveApiError, "下載上限"):
                download_drive_media("gdrive:lesson.mp3", Path(temp) / "source", max_bytes=1)
        urlopen.assert_not_called()

    def test_wrong_download_md5_and_oversized_content_are_rejected_and_removed(self):
        wrong = b"x" * len(self.data)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "source"
            with patch("app.jobs.drive_api._api_json", side_effect=self._api_responses()), \
                    patch("app.jobs.drive_api._refresh_access_token", return_value="test-token"), \
                    patch("app.jobs.drive_api.urllib.request.urlopen", return_value=io.BytesIO(wrong)):
                with self.assertRaisesRegex(DriveApiError, "不一致"):
                    download_drive_media("gdrive:lesson.mp3", target)
            self.assertFalse(target.exists())

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "source"
            with patch("app.jobs.drive_api._api_json", side_effect=self._api_responses()), \
                    patch("app.jobs.drive_api._refresh_access_token", return_value="test-token"), \
                    patch("app.jobs.drive_api.urllib.request.urlopen",
                          return_value=io.BytesIO(self.data + b"extra")):
                with self.assertRaisesRegex(DriveApiError, "超過 metadata 大小"):
                    download_drive_media("gdrive:lesson.mp3", target)
            self.assertFalse(target.exists())

    def test_path_control_characters_are_rejected(self):
        with self.assertRaisesRegex(DriveApiError, "控制字元"):
            download_drive_media("gdrive:lesson\n.mp3", Path("unused"))


class GoldenV2DriveIntakeTests(unittest.TestCase):
    def test_intake_registers_only_after_verified_download_and_cleans_temp_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "artifacts"
            root.mkdir()
            ledger = root / "ledger.sqlite"
            ledger.touch()
            observed = {}
            estimates = {name: 0 for name in (
                "gemini_primary", "chirp_independent", "compare",
                "third_optional", "adjudication", "golden_qa",
            )}

            def download(source_path, destination, *, max_bytes):
                destination.write_bytes(b"audio")
                return {
                    "source_path": source_path, "file_id": "file-id", "version": "2",
                    "size_bytes": 5, "downloaded_sha256": hashlib.sha256(b"audio").hexdigest(),
                }

            def register(**kwargs):
                observed.update(kwargs)
                self.assertTrue(kwargs["source_media"].is_file())
                return {"job_id": "v2p_" + "a" * 32, "status": "AWAITING_APPROVAL"}

            result = intake_drive_source(
                source_path="gdrive:lesson.mp3", artifact_root=root, database=ledger,
                mode="FLASH_CHIRP", estimated_stage_cents=estimates,
                estimate_ceiling_cents=1000, download=download, register=register,
            )
            self.assertFalse(observed["source_media"].exists())
            self.assertEqual(observed["drive_observation"]["file_id"], "file-id")
            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            self.assertTrue(result["drive_observation_authenticated"])
            self.assertFalse(result["provider_calls_started"])
            self.assertFalse(result["drive_mutation_started"])
            self.assertEqual(list(root.glob("drive-source-*")), [])

    def test_refuses_symlink_artifact_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir()
            link = Path(temp) / "link"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "cannot be a symlink"):
                intake_drive_source(
                    source_path="gdrive:a.mp3", artifact_root=link,
                    database=root / "ledger.sqlite", mode="FLASH_CHIRP",
                    estimated_stage_cents={}, estimate_ceiling_cents=1000,
                )

    def test_failed_drive_verification_never_calls_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "artifacts"
            root.mkdir()
            ledger = root / "ledger.sqlite"
            ledger.touch()
            register = Mock()

            def fail_download(source_path, destination, *, max_bytes):
                raise DriveApiError("version mismatch")

            with self.assertRaisesRegex(DriveApiError, "version mismatch"):
                intake_drive_source(
                    source_path="gdrive:lesson.mp3", artifact_root=root,
                    database=ledger, mode="FLASH_CHIRP",
                    estimated_stage_cents={}, estimate_ceiling_cents=1000,
                    download=fail_download, register=register,
                )
            register.assert_not_called()
            self.assertEqual(list(root.glob("drive-source-*")), [])

    def test_concurrent_intake_fails_fast_without_starting_another_download(self):
        download = Mock()
        with _INTAKE_LOCK, self.assertRaises(SourceIntakeBusyError):
            intake_drive_source(
                source_path="gdrive:lesson.mp3", artifact_root=Path("unused"),
                database=Path("unused/ledger.sqlite"), mode="FLASH_CHIRP",
                estimated_stage_cents={}, estimate_ceiling_cents=1000,
                download=download,
            )
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
