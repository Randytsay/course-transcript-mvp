"""No-cost V2 paid-dispatch review and atomic claim-race regression tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_paid_dispatch import preview_paid_dispatch
from app.pipeline.golden_v2_source_preflight import register_source_preflight


class GoldenV2PaidDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "artifacts"
        self.db = self.root / "ledger.sqlite"
        self.costs = {name: 0 for name in STAGES}
        self.costs.update(gemini_primary=30, chirp_independent=20, third_optional=10)
        self.job = self.create_source_job("first", approval=True)

    def create_source_job(self, tag: str, *, approval: bool, mode="QUALITY_3WAY"):
        source = Path(self.temp.name) / f"{tag}.mp3"
        source.write_bytes(f"fake Drive audio input {tag}".encode())
        observation = {
            "provider": "google_drive", "file_id": f"drive-{tag}",
            "version": "13", "size_bytes": source.stat().st_size,
            "downloaded_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }

        def fake_media(argv, **_):
            if argv[0] == "ffprobe":
                return subprocess.CompletedProcess(argv, 0, "8.5\n", "")
            Path(argv[-1]).write_bytes(f"normalized FLAC bytes {tag}".encode())
            return subprocess.CompletedProcess(argv, 0, "", "")

        registered = register_source_preflight(
            source_media=source, drive_observation=observation,
            artifact_root=self.root, database=self.db, mode=mode,
            estimated_stage_cents=self.costs, run=fake_media,
        )
        if approval:
            GoldenV2Ledger.open_existing(self.db).approve(
                registered["job_id"], revision=0, approved_cents=60,
                reference=f"test-reviewed-{tag}", actor="unit-test-owner",
            )
        return registered["job_id"]

    def preview(self, job=None, **kwargs):
        return preview_paid_dispatch(
            database=self.db, artifact_root=self.root,
            job_id=job or self.job, **kwargs,
        )

    def test_valid_local_preview_fingerprints_but_cannot_charge(self):
        before_db = self.db.read_bytes()
        one = self.preview()
        two = self.preview()
        self.assertEqual(one, two)
        self.assertEqual(one["blockers"], [])
        self.assertTrue(one["local_review_ready"])
        self.assertEqual(one["job_revision"], 1)
        self.assertEqual(one["stage"], "gemini_primary")
        self.assertEqual(one["proposed_provider_model"], "gemini-3.8-flash")
        self.assertEqual(one["stage_estimate_usd_cents"], 30)
        self.assertEqual(one["aggregate_exposure_usd_cents"], 60)
        self.assertEqual(len(one["review_fingerprint_sha256"]), 64)
        self.assertFalse(one["provider_calls_allowed"])
        self.assertFalse(one["owner_paid_call_authorization_verified"])
        self.assertFalse(one["live_drive_revision_verified"])
        self.assertEqual(self.db.read_bytes(), before_db)

    def test_global_cap_counts_multiple_job_reservations(self):
        second = self.create_source_job("second", approval=True)
        first = self.preview(aggregate_trial_cap_cents=100)
        self.assertIn("AGGREGATE_TRIAL_CAP_EXCEEDED", first["blockers"])
        self.assertIsNone(first["review_fingerprint_sha256"])
        self.assertEqual(first["aggregate_exposure_usd_cents"], 120)
        ready = self.preview(job=second, aggregate_trial_cap_cents=120)
        self.assertEqual(ready["blockers"], [])

    def test_unapproved_source_does_not_consume_global_cap(self):
        self.create_source_job("unapproved", approval=False)
        self.assertEqual(self.preview(aggregate_trial_cap_cents=60)["blockers"], [])
        self.assertEqual(self.preview()["aggregate_exposure_usd_cents"], 60)

    def test_known_unknown_provider_usage_holds_other_jobs(self):
        self.create_source_job("other", approval=True)
        ledger = GoldenV2Ledger.open_existing(self.db)
        claim = ledger.claim_next(self.job, worker_id="fake-provider")
        ledger.mark_outcome_unknown(claim)
        check = self.preview(job=self.job)
        self.assertIn("UNRECONCILED_GLOBAL_PAID_STAGE", check["blockers"])
        self.assertFalse(check["local_review_ready"])

    def test_stale_and_concurrent_claims_have_exactly_one_winner(self):
        preview = self.preview()
        kwargs = {
            "worker_id": "test-runner", "expected_stage": preview["stage"],
            "expected_revision": preview["job_revision"],
            "expected_source_sha": preview["source_sha256"],
            "aggregate_cap_cents": preview["aggregate_trial_cap_usd_cents"],
        }

        def race(_):
            try:
                return GoldenV2Ledger.open_existing(self.db).claim_next(
                    self.job, **kwargs,
                )
            except V2Conflict as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(race, range(2)))
        wins = [a for a in answers if isinstance(a, dict)]
        failures = [a for a in answers if isinstance(a, str)]
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(wins[0]["stage"], "gemini_primary")
        self.assertIn("revision changed", failures[0])
        now = self.preview()
        self.assertIn("NOT_A_PENDING_PAID_STAGE", now["blockers"])

    def test_ledger_source_and_revision_are_frozen_by_preview(self):
        preview = self.preview()
        with self.assertRaisesRegex(V2Conflict, "source SHA changed"):
            GoldenV2Ledger.open_existing(self.db).claim_next(
                self.job, worker_id="fake-worker",
                expected_stage="gemini_primary",
                expected_revision=preview["job_revision"],
                expected_source_sha="0" * 64,
                aggregate_cap_cents=1000,
            )
        self.assertEqual(self.preview()["review_fingerprint_sha256"],
                         preview["review_fingerprint_sha256"])

    def test_direct_cli_is_read_only_and_requires_no_pythonpath(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "golden_v2_paid_dispatch.py"
        result = subprocess.run(
            [sys.executable, str(script), "--database", str(self.db),
             "--artifact-root", str(self.root), "--job-id", self.job],
            cwd=self.temp.name, capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertTrue(record["local_review_ready"])
        self.assertFalse(record["provider_calls_allowed"])

    def test_missing_database_is_report_only(self):
        missing = self.root / "missing.sqlite"
        r = preview_paid_dispatch(
            database=missing, artifact_root=self.root, job_id=self.job,
        )
        self.assertIn("ISOLATED_LEDGER_NOT_READY", r["blockers"])
        self.assertIsNone(r["review_fingerprint_sha256"])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()