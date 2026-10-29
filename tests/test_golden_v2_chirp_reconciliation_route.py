"""Private API diagnostic entry: auth-before-I/O and zero paid side effects."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.golden_v2_chat_api import app


JOB = "v2p_" + "a" * 32


class ChirpReconciliationRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.sqlite"

    def test_preflight_verifies_auth_and_returns_readonly_evidence(self):
        proof = dict(job_id=JOB, source_sha256="b" * 64,
                     operation_count=34, local_evidence_reconciled=True,
                     live_gcp_provider_status_verified=False,
                     ledger_modified=False, read_only=True,
                     provider_calls_started=False,
                     drive_mutations_started=False, golden_qa_pass=False)
        with patch("app.golden_v2_chat_api._authorized_database",
                   return_value=self.db) as authorization, patch(
                "app.pipeline.golden_v2_chirp_reconciliation_preview."
                "preview_chirp_reconciliation",
                return_value=proof) as preview:
            response = self.client.get(
                f"/api/v1/golden-v2/jobs/{JOB}/chirp-reconciliation-preview",
                headers={"Authorization": "Bearer mock-private-client"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), proof)
            authorization.assert_called_once()
            preview.assert_called_once_with(
                artifact_root=self.db.parent, job_id=JOB)

    def test_invalid_job_rejected_without_preview(self):
        with patch("app.golden_v2_chat_api._authorized_database",
                   return_value=self.db), patch(
                "app.pipeline.golden_v2_chirp_reconciliation_preview."
                "preview_chirp_reconciliation") as preview:
            response = self.client.get(
                "/api/v1/golden-v2/jobs/invalid/chirp-reconciliation-preview")
            self.assertEqual(response.status_code, 422)
            preview.assert_not_called()

    def test_missing_job_and_unreadable_evidence_fail_closed(self):
        url = f"/api/v1/golden-v2/jobs/{JOB}/chirp-reconciliation-preview"
        for problem, expected in ((ValueError("unknown V2 job"), 404),
                                  (PermissionError("private directory"), 503),
                                  (ValueError("unsafe source directory"), 503)):
            with patch("app.golden_v2_chat_api._authorized_database",
                       return_value=self.db), patch(
                    "app.pipeline.golden_v2_chirp_reconciliation_preview."
                    "preview_chirp_reconciliation", side_effect=problem):
                response = self.client.get(url)
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("private directory", response.text)

    def test_auth_rejected_before_preview(self):
        url = f"/api/v1/golden-v2/jobs/{JOB}/chirp-reconciliation-preview"
        from fastapi import HTTPException
        with patch("app.golden_v2_chat_api._authorized_database",
                   side_effect=HTTPException(status_code=401,
                                             detail="Unauthorized")), patch(
                "app.pipeline.golden_v2_chirp_reconciliation_preview."
                "preview_chirp_reconciliation") as preview:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 401)
            preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
