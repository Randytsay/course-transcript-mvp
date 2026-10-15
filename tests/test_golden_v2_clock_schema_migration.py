"""Old Golden V2 ledger migrates with backup; no task, charge or receipt changes."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from scripts.golden_v2_migrate_clock_fix_schema import inspect_and_migrate


class ClockMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "ledger.sqlite"
        self.backups = self.base / "backups"
        self.backups.mkdir()
        ledger = GoldenV2Ledger(self.db)
        self.job_id = ledger.create(
            source_sha="a" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 5, "chirp_independent": 10,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            })
        ledger.approve(self.job_id, revision=0, approved_cents=15,
                       actor="test-only", reference="mock consent")
        # Old production ledger lacks the new local-clock-fix column.
        with sqlite3.connect(self.db) as c:
            c.execute("ALTER TABLE v2_chirp_operations DROP COLUMN local_clock_fix_sha")
            c.execute(
                """INSERT INTO v2_chirp_operations(
                job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,
                status,request_id,actual_cents,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (self.job_id, "chunk-0000", "a" * 64, "b" * 64,
                 "gs://private/mock", 0, 3000, "REVIEW_REQUIRED",
                 "mock-request", 5, "2026-10-15T01:00:00Z")
            )

    def test_dry_run_keeps_legacy_file_unchanged(self):
        before = self.db.read_bytes()
        report = inspect_and_migrate(self.db)
        self.assertEqual(report["action"], "MIGRATION_REQUIRED")
        self.assertEqual(report["before"]["recorded_chirp_cents"], 5)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_additive_migration_preserves_data_and_charges(self):
        before = inspect_and_migrate(self.db)
        result = inspect_and_migrate(self.db, backup_dir=self.backups, apply=True)
        self.assertEqual(result["action"], "MIGRATED")
        self.assertTrue(result["after"]["column_present"])
        self.assertEqual(result["after"]["data_digest"], before["before"]["data_digest"])
        self.assertEqual(result["after"]["recorded_chirp_cents"], 5)
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        with sqlite3.connect(backup) as b:
            self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(any(row[1] == "local_clock_fix_sha"
                                 for row in b.execute("PRAGMA table_info(v2_chirp_operations)")))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute(
                "SELECT status,actual_cents FROM v2_chirp_operations"
            ).fetchone(), ("REVIEW_REQUIRED", 5))
            self.assertEqual(c.execute(
                "SELECT status,approved_cents,revision FROM v2_jobs WHERE id=?",
                (self.job_id,)
            ).fetchone(), ("QUEUED", 15, 1))
        self.assertEqual(inspect_and_migrate(self.db,backup_dir=self.backups,apply=True)["action"],
                         "ALREADY_MIGRATED")

    def test_existing_backup_is_never_overwritten(self):
        import hashlib
        expected_name = "golden-v2-pre-clock-migration-" + hashlib.sha256(
            str(self.db.resolve()).encode()).hexdigest()[:12] + ".sqlite"
        existing = self.backups / expected_name
        existing.write_bytes(b"critical existing backup")
        with self.assertRaises(FileExistsError):
            inspect_and_migrate(self.db, backup_dir=self.backups, apply=True)
        self.assertEqual(existing.read_bytes(), b"critical existing backup")
        self.assertFalse(inspect_and_migrate(self.db)["before"]["column_present"])


if __name__ == "__main__":
    unittest.main()
