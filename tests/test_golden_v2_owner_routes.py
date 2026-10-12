"""Owner read-only status integration; no provider, production, or Drive access."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.golden_v2_routes import router
from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES


class OwnerReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "ledger.db"
        self.ledger = GoldenV2Ledger(self.db)
        self.job_id = self.ledger.create(
            source_sha="c" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={name: 0 for name in STAGES},
        )
        self.token = "test-unique-operator-secret-0123456789-0123456789"
        self.config = patch.dict(os.environ, {
            "COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED": "true",
            "COURSE_TRANSCRIPT_V2_STATUS_TOKEN": self.token,
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH": str(self.db),
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + self.token}

    def test_disabled_by_default_and_no_auth_fail_closed(self):
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED": "false"}):
            self.assertEqual(self.client.get("/api/v1/golden-v2/jobs").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/golden-v2/jobs").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/golden-v2/jobs", headers={
            "Authorization": "Bearer " + "0" * 48,
            "Cf-Access-Authenticated-User-Email": "owner@example.test",
            "Cf-Access-Jwt-Assertion": "unverified-header",
        }).status_code, 401)
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_STATUS_TOKEN": "weak"}):
            self.assertEqual(self.client.get("/api/v1/golden-v2/jobs", headers=self.headers).status_code, 503)

    def test_read_only_list_detail_no_sensitive_tokens_or_mutations(self):
        before = hashlib.sha256(self.db.read_bytes()).digest()
        response = self.client.get("/api/v1/golden-v2/jobs", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["jobs"]), 1)
        detail = self.client.get(f"/api/v1/golden-v2/jobs/{self.job_id}", headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["job"]["id"], self.job_id)
        self.assertEqual(len(payload["stages"]), len(STAGES))
        self.assertEqual(payload["provider_calls_started"], "UNKNOWN_WITHOUT_PROVIDER_MANIFEST")
        self.assertNotIn(self.token, str(payload))
        self.assertNotIn("approval_reference", str(payload))
        self.assertNotIn("claim_hash", str(payload))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(), before)
        self.assertEqual(self.client.post("/api/v1/golden-v2/jobs", headers=self.headers).status_code, 405)

    def test_missing_database_invalid_id_and_limit(self):
        self.assertEqual(self.client.get("/api/v1/golden-v2/jobs/v2_not-a-job", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/golden-v2/jobs?limit=101", headers=self.headers).status_code, 422)
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_LEDGER_PATH": str(self.db.with_suffix(".missing"))}):
            self.assertEqual(self.client.get("/api/v1/golden-v2/jobs", headers=self.headers).status_code, 503)

    def test_missing_schema_is_not_a_500_with_leak(self):
        empty = Path(self.temp.name) / "empty.db"
        with sqlite3.connect(empty):
            pass
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_LEDGER_PATH": str(empty)}):
            response = self.client.get("/api/v1/golden-v2/jobs", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("no such table", response.text)


if __name__ == "__main__":
    unittest.main()
