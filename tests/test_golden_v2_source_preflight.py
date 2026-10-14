"""Local source provenance and unapproved V2 registration tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_source_preflight import (
    SourcePreflightError, register_source_preflight,
)


def estimates() -> dict[str, int]:
    return {stage: 0 for stage in STAGES}


class GoldenV2SourcePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "downloaded-original.bin"
        self.source.write_bytes(b"private original audio bytes")
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.observation = {
            "provider": "google_drive", "file_id": "drive-file-123",
            "version": "19", "size_bytes": self.source.stat().st_size,
            "downloaded_sha256": source_sha,
        }
        self.artifacts = self.root / "artifacts"
        self.database = self.artifacts / "ledger.sqlite"
        self.normalized_bytes = b"deterministic normalized FLAC test bytes"
        self.ffmpeg_calls = 0

        def fake_ffmpeg(args, **kwargs):
            if args[0] == "ffprobe":
                return subprocess.CompletedProcess(args, 0, "8.25\n", "")
            self.ffmpeg_calls += 1
            Path(args[-1]).write_bytes(self.normalized_bytes)
            return subprocess.CompletedProcess(args, 0, "", "")

        self.fake_ffmpeg = fake_ffmpeg

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register(self, *, observation=None, costs=None):
        return register_source_preflight(
            source_media=self.source,
            drive_observation=observation or self.observation,
            artifact_root=self.artifacts,
            database=self.database,
            mode="QUALITY_3WAY",
            estimated_stage_cents=costs or estimates(),
            run=self.fake_ffmpeg,
        )

    def test_preflight_preserves_both_sources_and_registers_unapproved_job(self):
        result = self.register(costs={**estimates(), "gemini_primary": 125})

        self.assertEqual(result["status"], "AWAITING_APPROVAL")
        self.assertTrue(result["approval_required"])
        self.assertFalse(result["owner_approval_recorded"])
        self.assertFalse(result["drive_observation_authenticated"])
        self.assertFalse(result["source_bytes_uploaded"])
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutation_started"])
        self.assertEqual(result["estimated_cents"], 125)
        job_dir = Path(result["job_dir"])
        self.assertEqual((job_dir / "source.original").read_bytes(), self.source.read_bytes())
        self.assertEqual((job_dir / "normalized.flac").read_bytes(), self.normalized_bytes)
        self.assertEqual(
            hashlib.sha256((job_dir / "normalized.flac").read_bytes()).hexdigest(),
            result["source_sha256"],
        )
        package = json.loads((job_dir / "source-preflight.json").read_text())
        self.assertEqual(package["source_lineage_sha256"], result["source_lineage_sha256"])
        state = GoldenV2Ledger(self.database).status(result["job_id"])
        self.assertEqual(state["status"], "AWAITING_APPROVAL")
        with self.assertRaises(V2Conflict):
            GoldenV2Ledger(self.database).claim_next(
                result["job_id"], worker_id="preflight-test",
            )

    def test_replay_returns_same_job_without_duplicate_registration(self):
        first = self.register()
        second = self.register()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["registration_sha256"], second["registration_sha256"])
        self.assertEqual(self.ffmpeg_calls, 2)
        import sqlite3
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_jobs").fetchone()[0], 1)

    def test_new_drive_revision_gets_new_lineage_and_job_identity(self):
        first = self.register()
        revised = self.register(observation={**self.observation, "version": "20"})
        self.assertNotEqual(first["job_id"], revised["job_id"])
        self.assertNotEqual(first["source_lineage_sha256"], revised["source_lineage_sha256"])

    def test_drive_observation_must_match_actual_downloaded_bytes(self):
        wrong = {**self.observation, "downloaded_sha256": "0" * 64}
        with self.assertRaisesRegex(SourcePreflightError, "snapshot disagrees"):
            self.register(observation=wrong)
        self.assertFalse(self.database.exists())
        self.assertEqual(self.ffmpeg_calls, 0)

    def test_normalizer_reads_a_verified_snapshot_not_a_mutable_source_path(self):
        original = self.source.read_bytes()

        def mutating_ffmpeg(args, **kwargs):
            if args[0] == "ffprobe":
                return subprocess.CompletedProcess(args, 0, "8.25\n", "")
            self.ffmpeg_calls += 1
            self.assertEqual(Path(args[args.index("-i") + 1]).read_bytes(), original)
            Path(args[-1]).write_bytes(self.normalized_bytes)
            self.source.write_bytes(b"changed after verified snapshot")
            return subprocess.CompletedProcess(args, 0, "", "")

        result = register_source_preflight(
            source_media=self.source,
            drive_observation=self.observation,
            artifact_root=self.artifacts,
            database=self.database,
            mode="QUALITY_3WAY",
            estimated_stage_cents=estimates(),
            run=mutating_ffmpeg,
        )
        self.assertNotEqual(self.source.read_bytes(), original)
        self.assertEqual(
            (Path(result["job_dir"]) / "source.original").read_bytes(), original,
        )

    def test_rejects_incomplete_or_negative_stage_estimates_before_normalization(self):
        invalid = estimates()
        invalid.pop("chirp_independent")
        with self.assertRaisesRegex(SourcePreflightError, "per-stage estimates"):
            self.register(costs=invalid)
        invalid = estimates()
        invalid["chirp_independent"] = -1
        with self.assertRaisesRegex(SourcePreflightError, "per-stage estimates"):
            self.register(costs=invalid)
        self.assertEqual(self.ffmpeg_calls, 0)
        self.assertFalse(self.database.exists())

    def test_estimate_ceiling_is_not_bypassed_by_local_registration(self):
        with self.assertRaisesRegex(SourcePreflightError, "configured preflight ceiling"):
            register_source_preflight(
                source_media=self.source,
                drive_observation=self.observation,
                artifact_root=self.artifacts,
                database=self.database,
                mode="QUALITY_3WAY",
                estimated_stage_cents={**estimates(), "gemini_primary": 1001},
                run=self.fake_ffmpeg,
            )
        self.assertEqual(self.ffmpeg_calls, 0)
        self.assertFalse(self.database.exists())

    def test_refuses_existing_v1_or_mixed_database_without_modifying_it(self):
        import sqlite3
        self.artifacts.mkdir(parents=True)
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO jobs VALUES('preserve-me')")
        before = hashlib.sha256(self.database.read_bytes()).digest()
        with self.assertRaisesRegex(SourcePreflightError, "non-isolated or mixed"):
            self.register()
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).digest(), before)
        self.assertEqual(self.ffmpeg_calls, 0)

    def test_refuses_symlinked_jobs_directory_without_writing_outside_artifact_root(self):
        self.artifacts.mkdir(parents=True)
        outside = self.root / "outside-jobs"
        outside.mkdir()
        (self.artifacts / "jobs").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(SourcePreflightError, "jobs directory cannot be a symlink"):
            self.register()

        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.database.exists())

    def test_empty_normalizer_output_fails_before_job_registration(self):
        def empty_ffmpeg(args, **kwargs):
            Path(args[-1]).write_bytes(b"")
            return subprocess.CompletedProcess(args, 0, "", "")

        with self.assertRaisesRegex(SourcePreflightError, "non-empty FLAC"):
            register_source_preflight(
                source_media=self.source,
                drive_observation=self.observation,
                artifact_root=self.artifacts,
                database=self.database,
                mode="QUALITY_3WAY",
                estimated_stage_cents=estimates(),
                run=empty_ffmpeg,
            )
        self.assertFalse(self.database.exists())


if __name__ == "__main__":
    unittest.main()
