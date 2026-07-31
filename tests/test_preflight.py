from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.preflight import run_preflight
from app.jobs.store import JobStore


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.store = JobStore(self.data / "course-transcript.db")
        preview = self.store.create_preview(
            source_path="gdrive:課程/測試.mp3",
            source_name="測試.mp3",
            size_bytes=100,
            modified_at=None,
            mime_type="audio/mpeg",
            actor="owner@example.test",
        )
        self.job = self.store.create_preflight_job(
            preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="owner@example.test",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_non_paid_preflight_records_probe_and_removes_copy(self) -> None:
        def fake_run(
            command: list[str], *, timeout_seconds: int
        ) -> subprocess.CompletedProcess[str]:
            del timeout_seconds
            if command[0] == "rclone":
                Path(command[-1]).write_bytes(b"fake media")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch("app.jobs.preflight._run", side_effect=fake_run),
            patch(
                "app.jobs.preflight._probe",
                return_value={
                    "duration_seconds": 600.0,
                    "media_format": "mp3",
                    "audio_codec": "mp3",
                },
            ),
            patch("app.jobs.preflight._sha256", return_value="a" * 64),
        ):
            result = run_preflight(
                self.store,
                self.job,
                data_dir=self.data,
                worker_id="worker-test",
            )
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertEqual(result["source_checksum"], "a" * 64)
        self.assertGreater(float(result["estimated_cost_usd"]), 0)
        copied_files = list((self.data / "tmp" / "preflight").rglob("source.*"))
        self.assertEqual(copied_files, [])


if __name__ == "__main__":
    unittest.main()
