"""Owner status and approval API integration; no provider/Drive access."""
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


class OwnerApiTests(unittest.TestCase):
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
        self.write_token = "test-unique-write-secret-0123456789-0123456789"
        self.config = patch.dict(os.environ, {
            "COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED": "true",
            "COURSE_TRANSCRIPT_V2_STATUS_TOKEN": self.token,
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH": str(self.db),
            "COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "false",
            "COURSE_TRANSCRIPT_V2_WRITE_TOKEN": self.write_token,
            "COURSE_TRANSCRIPT_V2_OWNER_ACTOR": "owner@example.test",
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + self.token}
        self.write_headers = {"Authorization": "Bearer " + self.write_token}

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
        create_body = {
            "source_sha256": "a" * 64, "mode": "FLASH_CHIRP",
            "estimated_stage_cents": {name: 0 for name in STAGES},
            "confirm": True,
        }
        self.assertEqual(self.client.post("/api/v1/golden-v2/jobs", headers=self.headers,
                                         json=create_body).status_code, 404)

    def test_job_creation_is_write_gated_bounded_and_never_starts_work(self):
        estimates = {name: 0 for name in STAGES}
        estimates["gemini_primary"] = 7
        payload = {
            "source_sha256": "a" * 64, "mode": "FLASH_CHIRP",
            "estimated_stage_cents": estimates, "confirm": True,
        }
        url = "/api/v1/golden-v2/jobs"
        self.assertEqual(self.client.post(url, headers=self.write_headers,
                                          json=payload).status_code, 404)
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "true"}):
            self.assertEqual(self.client.post(url, headers=self.headers,
                                              json=payload).status_code, 401)
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**payload, "confirm": False}).status_code, 422)
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**payload, "source_sha256": "z" * 64}).status_code, 422)
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**payload, "estimated_stage_cents": {}}).status_code, 422)
            too_expensive = {**estimates, "gemini_primary": 1001}
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**payload, "estimated_stage_cents": too_expensive}).status_code, 422)
            response = self.client.post(url, headers=self.write_headers, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["status"], "AWAITING_APPROVAL")
        self.assertEqual(result["estimated_cents"], 7)
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutation_started"])
        state = self.ledger.status(result["job_id"])
        self.assertEqual(state["status"], "AWAITING_APPROVAL")
        self.assertIsNone(state["approved_cents"])
        self.assertEqual(state["estimated_cents"], 7)

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
        before = hashlib.sha256(empty.read_bytes()).digest()
        body = {"revision": 0, "approved_cents": 1,
                "reference": "test", "confirm": True}
        with patch.dict(os.environ, {
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH": str(empty),
            "COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "true",
        }):
            response = self.client.post(
                f"/api/v1/golden-v2/jobs/{self.job_id}/approval",
                headers=self.write_headers, json=body,
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(hashlib.sha256(empty.read_bytes()).digest(), before)
        create_body = {
            "source_sha256": "a" * 64, "mode": "FLASH_CHIRP",
            "estimated_stage_cents": {name: 0 for name in STAGES},
            "confirm": True,
        }
        with patch.dict(os.environ, {
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH": str(empty),
            "COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "true",
        }):
            response = self.client.post("/api/v1/golden-v2/jobs",
                                        headers=self.write_headers, json=create_body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(hashlib.sha256(empty.read_bytes()).digest(), before)

    def test_write_routes_are_separately_disabled_and_owner_authenticated(self):
        payload = {"revision": 0, "approved_cents": 1,
                   "reference": "owner-approved-test", "confirm": True}
        url = f"/api/v1/golden-v2/jobs/{self.job_id}/approval"
        self.assertEqual(self.client.post(url, headers=self.headers, json=payload).status_code, 404)
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "true"}):
            self.assertEqual(self.client.post(url, headers=self.headers, json=payload).status_code, 401)
            self.assertEqual(self.client.post(url, headers={
                "Authorization": "Bearer " + self.write_token,
                "Cf-Access-Authenticated-User-Email": "owner@example.test",
                "Cf-Access-Jwt-Assertion": "unverified-header",
            }, json=payload).status_code, 200)
        self.assertEqual(self.ledger.status(self.job_id)["status"], "QUEUED")

    def test_job_approval_requires_exact_cost_revision_and_confirmation(self):
        estimates = {name: 0 for name in STAGES}
        estimates["gemini_primary"] = 5
        job_id = self.ledger.create(
            source_sha="a" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents=estimates,
        )
        amount = {"revision": 0, "approved_cents": 5,
                  "reference": "source-and-cost-reviewed", "confirm": True}
        url = f"/api/v1/golden-v2/jobs/{job_id}/approval"
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "true"}):
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**amount, "extra": "rejected"}).status_code, 422)
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**amount, "confirm": False}).status_code, 422)
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**amount, "approved_cents": 4}).status_code, 409)
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json={**amount, "approved_cents": 1001}).status_code, 422)
            response = self.client.post(url, headers=self.write_headers, json=amount)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {
                "job_id": job_id, "status": "QUEUED", "revision": 1,
            })
            self.assertEqual(self.client.post(url, headers=self.write_headers,
                                              json=amount).status_code, 409)
            missing = self.client.post(
                "/api/v1/golden-v2/jobs/v2_00000000000000000000000000000000/approval",
                headers=self.write_headers, json=amount,
            )
            self.assertEqual(missing.status_code, 404)
        status = self.ledger.status(job_id)
        self.assertEqual(status["revision"], 1)
        self.assertEqual(status["approved_cents"], 5)

    def test_repair_approval_endpoint_persists_exact_plan_without_provider_calls(self):
        estimates = {name: 0 for name in STAGES}
        estimates["gemini_primary"] = 1
        estimates["chirp_independent"] = 10
        job_id = self.ledger.create(
            source_sha="d" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents=estimates,
        )
        self.ledger.approve(job_id, revision=0, approved_cents=11,
                            reference="base-test", actor="test")
        primary = self.ledger.claim_next(job_id, worker_id="fake")
        self.ledger.complete(primary, evidence_sha="a" * 64, actual_cents=1)
        chirp = self.ledger.claim_next(job_id, worker_id="fake")
        base_window = {
            "window_id": "chunk-000", "source_sha": "d" * 64,
            "clip_sha": "b" * 64, "gcs_uri": "gs://private/audio.flac",
            "start_ms": 0, "end_ms": 3000,
            "output_uri": "gs://private/out/", "estimated_cents": 2,
        }
        self.ledger.plan_chirp_window(chirp, base_window)
        self.ledger.begin_chirp_submit(chirp, "chunk-000")
        self.ledger.record_chirp_submission(chirp, "chunk-000", "operations/fake")
        self.ledger.recover_submitted_chirp(
            job_id, "chunk-000", operation_name="operations/fake",
            evidence_sha="c" * 64, actual_cents=1,
            verifier=lambda **row: row["clip_sha"] == "b" * 64,
        )
        state = self.ledger.status(job_id)
        assessment_sha = "e" * 64
        repair = {
            "window_id": "repair-r01-00", "source_sha": "d" * 64,
            "clip_sha": "f" * 64, "gcs_uri": "gs://private/repair.flac",
            "start_ms": 0, "end_ms": 3000,
            "output_uri": "gs://private/repair-output/", "estimated_cents": 1,
            "assessment_sha": assessment_sha,
            "target_intervals": [{"start_ms": 1000, "end_ms": 2000}],
            "strategy": "STANDARD_BATCH",
        }
        body = {
            "revision": state["revision"], "assessment_sha": assessment_sha,
            "windows": [repair], "approved_cents": 1,
            "reference": "exact-acoustic-plan-reviewed", "confirm": True,
        }
        url = f"/api/v1/golden-v2/jobs/{job_id}/repair-approval"
        before_calls = self.ledger.status(job_id)["spent_cents"]
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED": "true"}):
            response = self.client.post(url, headers=self.write_headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "APPROVED")
        self.assertEqual(response.json()["window_count"], 1)
        self.assertEqual(self.ledger.status(job_id)["spent_cents"], before_calls)
        self.assertEqual(self.ledger.chirp_windows(job_id)[0]["status"], "DONE")


if __name__ == "__main__":
    unittest.main()
