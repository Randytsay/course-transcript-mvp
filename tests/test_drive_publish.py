from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.jobs.drive_publish import (
    DrivePublishError,
    publish_outputs,
    source_parent_destination,
)


class DrivePublishTests(unittest.TestCase):
    def _job(self, root: Path) -> Path:
        job = root / "job"
        job.mkdir()
        (job / "subtitles-corrected.srt").write_text("subtitle", encoding="utf-8")
        (job / "transcript-corrected.txt").write_text("transcript", encoding="utf-8")
        (job / "chirp.json").write_text("{}\n", encoding="utf-8")
        (job / "transcript-segments.csv").write_text("raw_text,corrected_text,uncertain_terms\n", encoding="utf-8")
        return job

    def test_rate_limit_retries_one_file_and_persists_verified_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            job = self._job(Path(temp))
            commands: list[list[str]] = []
            delays: list[float] = []
            copy_attempts = 0

            def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
                nonlocal copy_attempts
                commands.append(command)
                if command[1] == "copyto":
                    copy_attempts += 1
                    if copy_attempts == 1:
                        return subprocess.CompletedProcess(command, 1, "", "googleapi: Error 403: rateLimitExceeded")
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(command, 0, '{"bytes": 8}', "")

            state = publish_outputs(
                job,
                source_name="lesson.mp3",
                destination="gdrive:course/output",
                output_formats=["srt"],
                authorized=True,
                runner=runner,
                sleeper=delays.append,
                jitter=lambda: 0.0,
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["files"]["srt"]["status"], "completed")
            self.assertEqual(state["files"]["srt"]["attempts"], 2)
            self.assertTrue(any(delay == 30.0 for delay in delays))
            self.assertGreaterEqual(sum(delay > 0 for delay in delays), 3)
            upload = next(command for command in commands if command[1] == "copyto")
            self.assertIn("--checksum", upload)
            self.assertEqual(upload[upload.index("--tpslimit") + 1], "1")
            self.assertEqual(upload[-1], "gdrive:course/output/lesson.srt")
            persisted = json.loads((job / "drive-publish-state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["files"]["srt"]["remote_bytes"], 8)

    def test_explicit_authorization_is_required_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            job = self._job(Path(temp))
            with self.assertRaises(DrivePublishError):
                publish_outputs(
                    job,
                    source_name="lesson.mp3",
                    destination="gdrive:course/output",
                    output_formats=["srt"],
                    authorized=False,
                )
            self.assertFalse((job / "drive-publish-state.json").exists())

    def test_json_sidecar_is_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            job = self._job(Path(temp))

            def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
                if command[1] == "copyto":
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(command, 0, '{"bytes": 3}', "")

            result = publish_outputs(
                job,
                source_name="lesson.mp3",
                destination="gdrive:course/output",
                output_formats=["json"],
                authorized=True,
                runner=runner,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["files"]["json"]["local_name"], "chirp.json")

    def test_completed_state_resumes_without_another_drive_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            job = self._job(Path(temp))
            state_path = job / "drive-publish-state.json"
            state_path.write_text(json.dumps({
                "version": 1,
                "destination": "gdrive:course/output",
                "source_name": "lesson.mp3",
                "files": {"srt": {"status": "completed"}},
            }), encoding="utf-8")

            def forbidden(_: list[str]) -> subprocess.CompletedProcess[str]:
                raise AssertionError("completed publication must not call Drive")

            result = publish_outputs(
                job,
                source_name="lesson.mp3",
                destination="gdrive:course/output",
                output_formats=["srt"],
                authorized=True,
                runner=forbidden,
            )
            self.assertEqual(result["status"], "completed")

    def test_source_parent_destination_keeps_generated_files_beside_source(self) -> None:
        self.assertEqual(
            source_parent_destination("gdrive:課程/女性保健/lesson.m4a"),
            "gdrive:課程/女性保健",
        )
        self.assertEqual(source_parent_destination("gdrive:lesson.m4a"), "gdrive:")
        with self.assertRaises(DrivePublishError):
            source_parent_destination("gdrive:課程/../lesson.m4a")
