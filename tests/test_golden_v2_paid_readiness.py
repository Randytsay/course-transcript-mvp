"""Provider-free, mutation-free admission checks for the future paid worker."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES
from app.pipeline.golden_v2_paid_readiness import inspect_paid_readiness
from app.pipeline.golden_v2_source_preflight import register_source_preflight


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PaidReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "golden-v2"
        self.db = self.root / "ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.db)
        self.original = b"mock original, never uploaded"
        self.flac = b"mock normalized flac, never sent"
        self.source_sha = sha(self.flac)
        self.estimates = {stage: 0 for stage in STAGES}
        self.estimates["gemini_primary"] = 25
        self.estimates["chirp_independent"] = 15
        self.drive = {
            "provider": "google_drive", "file_id": "mock-id",
            "version": "mock-version", "downloaded_sha256": sha(self.original),
            "size_bytes": len(self.original),
        }
        lineage = {
            "normalized_sha256": self.source_sha,
            "original_sha256": sha(self.original),
            "drive_observation": self.drive,
        }
        lineage_bytes = json.dumps(lineage).encode()
        preflight = {
            "schema": "golden-v2-source-preflight-v1",
            "source_sha256": self.source_sha,
            "original_sha256": sha(self.original),
            "source_lineage_sha256": sha(lineage_bytes),
            "drive_observation": self.drive,
            "mode": "QUALITY_3WAY",
            "estimated_stage_cents": self.estimates,
            "estimates_are_authorization": False,
            "estimates_are_provider_quotes": False,
            "owner_approval_required": True,
        }
        preflight_bytes = json.dumps(
            preflight, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.job = self.ledger.create_from_preflight(
            source_sha=self.source_sha, mode="QUALITY_3WAY",
            estimated_stage_cents=self.estimates,
            registration_sha256=sha(preflight_bytes),
        )
        self.dir = self.root / "jobs" / self.job
        self.dir.mkdir(parents=True)
        (self.dir / "source.original").write_bytes(self.original)
        (self.dir / "normalized.flac").write_bytes(self.flac)
        (self.dir / "source-lineage.json").write_bytes(lineage_bytes)
        (self.dir / "source-preflight.json").write_bytes(preflight_bytes)

    def inspect(self, **extra):
        return inspect_paid_readiness(
            database=self.db, artifact_root=self.root, job_id=self.job, **extra,
        )

    def approve(self):
        self.ledger.approve(
            self.job, revision=0, approved_cents=40,
            reference="reviewed-test-budget", actor="test-owner",
        )

    def test_approved_valid_snapshot_is_still_not_paid_permission(self):
        self.approve()
        previous = self.db.read_bytes()
        source = (self.dir / "normalized.flac").read_bytes()
        report = self.inspect(expected_source_sha=self.source_sha)
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["source_snapshot_ready"])
        self.assertEqual(report["next_stage"], "gemini_primary")
        self.assertEqual(report["next_action"], "REVIEW_PROVIDER_AND_LIVE_SOURCE")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["provider_calls_allowed"])
        self.assertFalse(report["live_drive_revision_verified"])
        self.assertFalse(report["provider_pricing_verified"])
        self.assertEqual(self.db.read_bytes(), previous)
        self.assertEqual((self.dir / "normalized.flac").read_bytes(), source)
        self.assertEqual(self.ledger.status(self.job)["stages"][0]["state"], "PENDING")

    def test_unapproved_task_never_is_ready(self):
        self.assertIn("OWNER_APPROVAL_NOT_ACTIVE", self.inspect()["blockers"])

    def test_tampered_media_and_lineage_are_blocked(self):
        self.approve()
        (self.dir / "normalized.flac").write_bytes(b"tampered")
        self.assertIn("SOURCE_LINEAGE_VERIFICATION_FAILED", self.inspect()["blockers"])
        (self.dir / "normalized.flac").write_bytes(self.flac)
        (self.dir / "source-preflight.json").write_text("{")
        self.assertIn("SOURCE_PREFLIGHT_MISSING_OR_CORRUPT", self.inspect()["blockers"])

    def test_changed_preflight_registration_and_drive_metadata_block(self):
        self.approve()
        path = self.dir / "source-preflight.json"
        data = json.loads(path.read_text())
        data["drive_observation"]["version"] = "another-drive-revision"
        path.write_text(json.dumps(data))
        self.assertIn("SOURCE_LINEAGE_VERIFICATION_FAILED", self.inspect()["blockers"])

    def test_unsafe_jobs_root_is_rejected(self):
        self.approve()
        jobs = self.root / "jobs"
        external = self.root / "elsewhere"
        jobs.rename(external)
        jobs.symlink_to(external, target_is_directory=True)
        self.assertIn("JOB_ARTIFACT_DIRECTORY_INVALID", self.inspect()["blockers"])

    def test_real_preflight_serializer_can_pass_read_only_inspection(self):
        # Exercise the production preflight writer rather than hand-crafting
        # JSON only; actual audio conversion is injected to remain no-cost.
        source = Path(self.temp.name) / "downloaded.mp3"
        source.write_bytes(b"mock selected audio from Drive")
        real_root = Path(self.temp.name) / "separate-artifacts"
        database = real_root / "ledger.sqlite"
        observation = {
            "provider": "google_drive", "file_id": "drive-audio-123",
            "version": "7", "size_bytes": source.stat().st_size,
            "downloaded_sha256": sha(source.read_bytes()),
        }

        def fake_media_tool(argv, **_unused):
            if argv[0] == "ffprobe":
                return subprocess.CompletedProcess(argv, 0, "8.25\n", "")
            Path(argv[-1]).write_bytes(b"mock fake ffmpeg output")
            return subprocess.CompletedProcess(argv, 0, "", "")

        registration = register_source_preflight(
            source_media=source, drive_observation=observation,
            artifact_root=real_root, database=database,
            mode="QUALITY_3WAY", estimated_stage_cents=self.estimates,
            run=fake_media_tool,
        )
        self.assertEqual(registration["status"], "AWAITING_APPROVAL")
        ledger = GoldenV2Ledger.open_existing(database)
        ledger.approve(
            registration["job_id"], revision=0, approved_cents=40,
            reference="unit-test-source-and-cost", actor="test-owner",
        )
        report = inspect_paid_readiness(
            database=database, artifact_root=real_root,
            job_id=registration["job_id"],
            expected_source_sha=registration["source_sha256"],
        )
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["source_snapshot_ready"])
        self.assertFalse(report["provider_calls_allowed"])

    def test_symlinked_source_is_blocked(self):
        self.approve()
        (self.dir / "normalized.flac").unlink()
        (self.dir / "normalized.flac").symlink_to(self.dir / "source.original")
        self.assertIn("SOURCE_PREFLIGHT_MISSING_OR_CORRUPT", self.inspect()["blockers"])

    def test_prior_paid_receipt_blocks_any_new_attempt(self):
        self.approve()
        folder = self.dir / "gemini-primary"
        folder.mkdir()
        (folder / "legacy.json").write_text('{"status":"REQUEST_STARTED"}')
        self.assertIn("EXISTING_PAID_RECEIPTS_RECONCILE", self.inspect()["blockers"])

    def test_ambiguous_paid_outcome_holds_stage(self):
        self.approve()
        folder = self.dir / "golden-v2"
        folder.mkdir()
        (folder / "primary-ambiguous.json").write_text('{"unknown":true}')
        self.assertIn("PAID_OUTCOME_HOLD_RECONCILE", self.inspect()["blockers"])

    def test_chirp_operation_identity_requires_reconciliation(self):
        self.approve()
        claim = self.ledger.claim_next(self.job, worker_id="mock-primary")
        self.ledger.complete(claim, evidence_sha="e" * 64, actual_cents=0)
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO v2_chirp_operations("
                "job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,"
                "end_ms,status,request_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.job, "w0", self.source_sha, "c" * 64,
                 "gs://mock/w0", 0, 1000, "SUBMITTING",
                 "immutable-request-1", "2026-10-12T00:00:00+00:00"),
            )
        report = self.inspect()
        self.assertEqual(report["next_stage"], "chirp_independent")
        self.assertIn("EXISTING_CHIRP_OPERATION_RECONCILE", report["blockers"])

    def test_cannot_expand_cost_ceiling_or_change_source(self):
        self.approve()
        self.assertIn("BUDGET_APPROVAL_NOT_VERIFIED",
                      self.inspect(max_approved_cents=30)["blockers"])
        self.assertIn("SOURCE_SHA_MISMATCH",
                      self.inspect(expected_source_sha="f" * 64)["blockers"])

    def test_existing_running_paid_claim_must_not_be_reclaimed(self):
        self.approve()
        self.ledger.claim_next(self.job, worker_id="existing-owner")
        self.assertIn("NOT_A_PENDING_PAID_STAGE", self.inspect()["blockers"])

    def test_reject_path_escape_and_missing_db_without_creation(self):
        self.approve()
        with self.assertRaises(ValueError):
            inspect_paid_readiness(
                database=self.db, artifact_root=self.root,
                job_id="../../etc/passwd",
            )
        absent = self.root / "not-created.sqlite"
        report = inspect_paid_readiness(
            database=absent, artifact_root=self.root, job_id=self.job,
        )
        self.assertIn("ISOLATED_LEDGER_NOT_READY", report["blockers"])
        self.assertFalse(absent.exists())

    def test_direct_cli_can_report_blocker_without_pythonpath(self):
        script = Path(__file__).resolve().parents[1] / "scripts/golden_v2_paid_readiness.py"
        process = subprocess.run(
            [sys.executable, str(script), "--database", str(self.db),
             "--artifact-root", str(self.root), "--job-id", self.job],
            cwd=self.temp.name, capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(process.returncode, 2, process.stderr)
        report = json.loads(process.stdout)
        self.assertIn("OWNER_APPROVAL_NOT_ACTIVE", report["blockers"])
        self.assertFalse(report["provider_calls_allowed"])


if __name__ == "__main__":
    unittest.main()