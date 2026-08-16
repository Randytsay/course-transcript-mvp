from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.operations.production_health import build_report, write_report_atomic


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
                updated_at TEXT NOT NULL,
                lease_expires_at TEXT,
                last_heartbeat_at TEXT,
                locked_by TEXT
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
            "INSERT INTO jobs (id, status, active_stage, stage_detail, error, updated_at, lease_expires_at, last_heartbeat_at, locked_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                status,
                active_stage,
                detail,
                error,
                (self.now - timedelta(hours=age_hours)).isoformat(),
                None,
                None,
                None,
            ),
        )
        connection.commit()
        connection.close()

    def write_strategy(self, job_id: str, strategy: str) -> None:
        job_dir = self.data_dir / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "chunk-plan.json").write_text(
            json.dumps({"processing_strategy": strategy}),
            encoding="utf-8",
        )

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

    def test_awaiting_confirmation_does_not_require_worker_heartbeat(self) -> None:
        self.insert_job(
            "approval",
            status="awaiting_confirmation",
            active_stage="cost_confirmation",
            age_hours=30,
        )
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE jobs SET last_heartbeat_at = ? WHERE id = 'approval'",
            ((self.now - timedelta(hours=30)).isoformat(),),
        )
        connection.commit()
        connection.close()

        report = build_report(self.data_dir, now=self.now)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["counts"]["active"], 1)
        self.assertEqual(report["counts"]["stale_heartbeats"], 0)

    def test_machine_strategy_works_without_chinese_stage_detail(self) -> None:
        self.insert_job(
            "english",
            status="transcribing",
            active_stage="chirp",
            detail="Submitted; waiting for provider capacity",
            age_hours=19,
        )
        self.write_strategy("english", "DYNAMIC_BATCHING")
        report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["counts"]["dynamic_waiting"], 1)
        self.assertEqual(report["findings"][0]["job_id"], "english")

    def test_machine_strategy_overrides_legacy_text(self) -> None:
        self.insert_job(
            "standard",
            status="transcribing",
            active_stage="chirp",
            detail="Chirp 動態批次舊文案",
            age_hours=30,
        )
        self.write_strategy("standard", "PROCESSING_STRATEGY_UNSPECIFIED")
        report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["counts"]["dynamic_waiting"], 0)

    def test_custom_sla_appears_in_message(self) -> None:
        self.insert_job(
            "custom",
            status="transcribing",
            active_stage="chirp",
            detail="Submitted",
            age_hours=21,
        )
        self.write_strategy("custom", "DYNAMIC_BATCHING")
        with patch.dict(
            "os.environ",
            {
                "DYNAMIC_BATCH_WARNING_HOURS": "10",
                "DYNAMIC_BATCH_CRITICAL_HOURS": "18",
                "DYNAMIC_BATCH_SLA_HOURS": "20",
            },
        ):
            report = build_report(self.data_dir, now=self.now)
        self.assertEqual(report["status"], "critical")
        self.assertIn("20 小時", report["findings"][0]["message"])

    def test_atomic_output_updates_for_warning_and_critical(self) -> None:
        output = self.data_dir / "production-health.json"
        for status in ("warning", "critical"):
            report = {
                "status": status,
                "generated_at": self.now.isoformat(),
                "data_dir": str(self.data_dir),
                "counts": {},
                "findings": [],
            }
            write_report_atomic(report, output)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], status)
            self.assertFalse(any(output.parent.glob(f".{output.name}.*.tmp")))

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
