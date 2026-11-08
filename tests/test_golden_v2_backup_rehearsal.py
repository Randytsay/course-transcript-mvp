"""Offline-only exact restore drill: never open real Production paths."""
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from scripts.golden_v2_backup_rehearsal import create_bundle, verify_bundle


LABEL = "pr159-20261029T103500Z"


class GoldenV2BackupRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "golden-v2"
        self.root.mkdir()
        self.backups = Path(self.temp.name) / "protected-backups"
        self.backups.mkdir(mode=0o700)
        self.ledger = GoldenV2Ledger(self.root / "ledger.sqlite")
        self.job = self.ledger.create_from_preflight(
            source_sha="a" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents=dict(
                gemini_primary=100, chirp_independent=78,
                compare=0, third_optional=0, adjudication=0, golden_qa=0),
            registration_sha256="d" * 64)
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.jobdir = self.jobs / self.job
        self.jobdir.mkdir()
        (self.jobdir / "normalized.flac").write_bytes(b"fake source")
        (self.jobdir / "golden-v2").mkdir()
        (self.jobdir / "golden-v2" / "window.json").write_bytes(
            b'{"operation_name":"operations/known"}')

    def test_backup_and_actual_isolated_restore_sha_match(self):
        backup = create_bundle(root=self.root, backups=self.backups, label=LABEL)
        bundle = self.backups / LABEL
        self.assertEqual(bundle.as_posix(), backup["snapshot"])
        self.assertTrue(backup["restore_drill_pass"])
        self.assertEqual(backup["file_count"], 2)
        self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o700)
        verified = verify_bundle(bundle)
        self.assertTrue(verified["restore_drill_pass"])
        self.assertEqual(verified["ledger_sha256"], backup["ledger_sha256"])
        self.assertEqual(verified["file_count"], 2)
        self.assertEqual(self.ledger.status(self.job)["revision"], 0)
        self.assertFalse((self.root / "jobs" / self.job / "golden-v2" / "restore").exists())
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_bundle(root=self.root, backups=self.backups, label=LABEL)

    def test_backup_detects_tampered_artifacts_and_fails_closed(self):
        create_bundle(root=self.root, backups=self.backups, label=LABEL)
        bundle = self.backups / LABEL
        (bundle / "jobs.tar.gz").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "backup SHA mismatch"):
            verify_bundle(bundle)

    def test_source_and_label_path_restrictions(self):
        with self.assertRaisesRegex(ValueError, "invalid immutable backup label"):
            create_bundle(root=self.root, backups=self.backups,
                          label="../../backups/unsafe")
        with self.assertRaisesRegex(ValueError, "outside live artifact root"):
            create_bundle(root=self.root, backups=self.root,
                          label=LABEL)
        (self.jobdir / "unsafe-link").symlink_to(self.root / "ledger.sqlite")
        with self.assertRaisesRegex(ValueError, "symlink in job artifacts"):
            create_bundle(root=self.root, backups=self.backups, label=LABEL)
        self.assertFalse((self.backups / LABEL).exists())

    def test_consistent_sqlite_backup_when_live_ledger_is_wal(self):
        db_path = self.root / "ledger.sqlite"
        with sqlite3.connect(db_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
        saved = create_bundle(root=self.root, backups=self.backups, label=LABEL)
        self.assertTrue(saved["restore_drill_pass"])
        with sqlite3.connect(self.backups / LABEL / "ledger.sqlite") as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM v2_jobs").fetchone()[0], 1)

    def test_no_credentials_or_paid_submission_required(self):
        info = create_bundle(root=self.root, backups=self.backups, label=LABEL)
        manifest = json.loads(
            (Path(info["snapshot"]) / "manifest.json").read_text())
        self.assertFalse(manifest["live_jobs_mutated"])
        self.assertFalse(manifest["live_ledger_mutated"])
        self.assertFalse(manifest["provider_calls_started"])
        self.assertFalse(manifest["drive_mutations_started"])


if __name__ == "__main__":
    unittest.main()
