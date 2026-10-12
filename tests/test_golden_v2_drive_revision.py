"""Live Drive metadata must still match the approved original before payment."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES
from app.pipeline.golden_v2_drive_revision import verify_live_drive_revision
from app.pipeline.golden_v2_source_preflight import register_source_preflight


class DriveRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "artifacts"
        self.db = self.root / "ledger.sqlite"
        self.media = Path(self.temp.name) / "lesson.mp3"
        self.media.write_bytes(b"fixed course audio bytes")
        self.md5 = hashlib.md5(self.media.read_bytes(), usedforsecurity=False).hexdigest()
        self.observation = {
            "provider": "google_drive", "source_path": "gdrive:lesson.mp3",
            "file_id": "drive-source-1", "version": "17",
            "size_bytes": self.media.stat().st_size,
            "downloaded_sha256": hashlib.sha256(self.media.read_bytes()).hexdigest(),
            "downloaded_md5": self.md5,
        }
        costs = {stage: 0 for stage in STAGES}
        costs.update(gemini_primary=30, chirp_independent=20)

        def fake_media(argv, **_):
            if argv[0] == "ffprobe":
                return subprocess.CompletedProcess(argv, 0, "8.5\n", "")
            Path(argv[-1]).write_bytes(b"normalized FLAC source")
            return subprocess.CompletedProcess(argv, 0, "", "")

        registered = register_source_preflight(
            source_media=self.media, drive_observation=self.observation,
            artifact_root=self.root, database=self.db, mode="FLASH_CHIRP",
            estimated_stage_cents=costs, run=fake_media,
        )
        self.job = registered["job_id"]
        self.ledger = GoldenV2Ledger.open_existing(self.db)
        self.ledger.approve(
            self.job, revision=0, approved_cents=50,
            reference="approved-source-17", actor="owner-test",
        )

    def inspect(self, reader):
        return verify_live_drive_revision(
            database=self.db, artifact_root=self.root,
            job_id=self.job, read_metadata=reader,
        )

    def live(self, _):
        return {"id": "drive-source-1", "version": "17",
                "size_bytes": self.media.stat().st_size,
                "md5_checksum": self.md5}

    def test_matching_live_source_is_read_only_and_still_not_paid_permission(self):
        before = self.ledger.status(self.job)
        result = self.inspect(self.live)
        self.assertEqual(result["blockers"], [])
        self.assertTrue(result["live_drive_revision_verified"])
        self.assertFalse(result["provider_calls_allowed"])
        self.assertEqual(self.ledger.status(self.job), before)

    def test_changed_version_or_object_blocks(self):
        for change in ({"version": "18"}, {"id": "different"},
                       {"size_bytes": 1}, {"md5_checksum": "0" * 32}):
            with self.subTest(change=change):
                result = self.inspect(lambda _: {**self.live(None), **change})
                self.assertIn("LIVE_DRIVE_SOURCE_CHANGED", result["blockers"])
                self.assertFalse(result["live_drive_revision_verified"])

    def test_drive_read_error_fails_closed(self):
        def unavailable(_):
            raise RuntimeError("temporary Drive failure")
        result = self.inspect(unavailable)
        self.assertIn("LIVE_DRIVE_REVISION_UNAVAILABLE", result["blockers"])

    def test_local_original_tampering_prevents_drive_call(self):
        (self.root / "jobs" / self.job / "source.original").write_bytes(b"changed")
        calls = []
        result = self.inspect(lambda path: calls.append(path))
        self.assertEqual(calls, [])
        self.assertIn("SOURCE_LINEAGE_VERIFICATION_FAILED", result["blockers"])


if __name__ == "__main__":
    unittest.main()
