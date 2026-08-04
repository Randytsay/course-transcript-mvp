from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.operations.production_health import build_report


class ProductionHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.database = self.data_dir / "course-transcript.db"
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_stage TEXT,
                stage_detail TEXT,
                error TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
        connection.close()
        self.now = datetime(2026, 8, 5, 0, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def insert_job(
        self,
        job_id: str,
        *,
        status: str,
        active_stage: str | None = None,
        detail: str | None = None,
        error: str | None = None,
        age_hours: float = 0,
    ) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)",
            (
                job_id,
                status,
                active_stage,
                detail,
                error,
                (self.now - timedelta(hours=age_hours)).isoformat(),
            ),
        )
        connection.commit()
        connection.close()

    def test_healthy_database_is_ok(self) -> None:
        self.insert_job("done", status="completed")
        report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["counts"]["jobs"], 1)

    def test_dynamic_batch_warning_and_critical(self) -> None:
        self.insert_job(
            "slow",
            status="transcribing",
            active_stage="chirp",
            detail="Chirp 動態批次已提交；等待 Google 離峰處理",
            age_hours=19,
        )
        report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["findings"][0]["code"], "dynamic_batch_delayed")

        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = 'slow'",
            ((self.now - timedelta(hours=24, minutes=1)).isoformat(),),
        )
        connection.commit()
        connection.close()
        report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "critical")
        self.assertEqual(report["findings"][0]["code"], "dynamic_batch_sla_breach")

    def test_drive_retry_is_reported_without_provider_retry(self) -> None:
        self.insert_job("drive", status="completed")
        job_dir = self.data_dir / "jobs" / "drive"
        job_dir.mkdir(parents=True)
        (job_dir / "drive-delivery-state.json").write_text(
            json.dumps({"status": "pending_retry", "attempts": 5}),
            encoding="utf-8",
        )
        report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "critical")
        self.assertEqual(report["counts"]["drive_pending"], 1)
        self.assertIn("付費辨識不可重跑", report["findings"][0]["message"])

    def test_missing_database_is_critical(self) -> None:
        report = build_report(self.data_dir / "missing", now=self.now)
        self.assertEqual(report["status"], "critical")
        self.assertEqual(report["findings"][0]["code"], "database_missing")


if __name__ == "__main__":
    unittest.main()
