from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient


SA_A = {
    "type": "service_account",
    "project_id": "proj-a",
    "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
    "client_email": "sa-a@proj-a.iam.gserviceaccount.com",
}


class AIAccountsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        os.environ["AI_ACCOUNTS_DIR"] = str(self.tmp / "ai-accounts")
        os.environ["GCP_CREDENTIALS_TARGET_PATH"] = str(self.tmp / "mounted" / "gcp-sa.json")
        self.addCleanup(os.environ.pop, "AI_ACCOUNTS_DIR", None)
        self.addCleanup(os.environ.pop, "GCP_CREDENTIALS_TARGET_PATH", None)

        # Drop cached modules so env vars take effect.
        for mod in list(sys.modules):
            if mod.startswith("app.review.admin") or mod.startswith("app.review.ai_accounts"):
                del sys.modules[mod]

        import app.review.admin as admin_module
        importlib.reload(admin_module)

        class FakeStore:
            @contextlib.contextmanager
            def transaction(self):
                class _Conn:
                    def execute(self, *_a, **_k):
                        return None
                yield _Conn()

        from app.review import admin as admin_module

        patcher = mock.patch.object(admin_module, "_store", return_value=FakeStore())
        patcher.start()
        self.addCleanup(patcher.stop)

        app = FastAPI()
        app.include_router(admin_module.router)
        self.client = TestClient(app)

    def test_list_empty_profiles(self) -> None:
        r = self.client.get("/api/v1/review-admin/ai-accounts")
        assert r.status_code == 200
        body = r.json()
        assert body["profiles"] == []
        assert "GCP Project" in body["billing_note"]

    def test_add_profile_with_location_and_bucket(self) -> None:
        r = self.client.post(
            "/api/v1/review-admin/ai-accounts",
            json={"name": "prof-x", "sa_json": SA_A,
                  "location": "asia-east1", "gcs_bucket": "my-bucket"},
        )
        assert r.status_code == 200
        assert r.json()["project_id"] == "proj-a"  # from JSON, not frontend

    def test_frontend_project_id_cannot_override_json(self) -> None:
        r = self.client.post(
            "/api/v1/review-admin/ai-accounts",
            json={"name": "prof-y", "sa_json": SA_A},
        )
        listing = self.client.get("/api/v1/review-admin/ai-accounts").json()
        profile = next(p for p in listing["profiles"] if p["name"] == "prof-y")
        assert profile["project_id"] == SA_A["project_id"]

    def test_preflight_then_switch_flow(self) -> None:
        self.client.post("/api/v1/review-admin/ai-accounts",
                         json={"name": "pf", "sa_json": SA_A})

        import app.review.admin as am
        ok_checks = {"ok": True, "errors": [], "checks": {"token_mint": "ok"}}
        with mock.patch.object(am, "run_live_checks", lambda c, m: ok_checks):
            pre = self.client.post("/api/v1/review-admin/ai-accounts/preflight",
                                   json={"name": "pf"}).json()
            assert pre["ok"] is True

            # switch without confirm is rejected
            r = self.client.post("/api/v1/review-admin/ai-accounts/switch",
                                 json={"name": "pf"})
            assert r.status_code == 422

            r2 = self.client.post("/api/v1/review-admin/ai-accounts/switch",
                                  json={"name": "pf", "confirm": True})
            assert r2.status_code == 200
        target = Path(os.environ["GCP_CREDENTIALS_TARGET_PATH"])
        assert json.loads(target.read_text())["project_id"] == "proj-a"

        listing = self.client.get("/api/v1/review-admin/ai-accounts").json()
        assert listing["active"] == "pf"
        dumped = json.dumps(listing)
        assert "BEGIN PRIVATE KEY" not in dumped and "private_key" not in dumped

    def test_rollback_endpoint(self) -> None:
        sa_b = dict(SA_A, project_id="proj-b",
                    client_email="sa-b@proj-b.iam.gserviceaccount.com")
        self.client.post("/api/v1/review-admin/ai-accounts",
                         json={"name": "one", "sa_json": SA_A})
        self.client.post("/api/v1/review-admin/ai-accounts",
                         json={"name": "two", "sa_json": sa_b})
        import app.review.admin as am
        ok_checks = {"ok": True, "errors": [], "checks": {"token_mint": "ok"}}
        with mock.patch.object(am, "run_live_checks", lambda c, m: ok_checks):
            self.client.post("/api/v1/review-admin/ai-accounts/switch",
                             json={"name": "one", "confirm": True})
            self.client.post("/api/v1/review-admin/ai-accounts/switch",
                             json={"name": "two", "confirm": True})
        r = self.client.post("/api/v1/review-admin/ai-accounts/rollback")
        assert r.status_code == 200
        assert r.json()["rolled_back"] is True
        target = json.loads(Path(os.environ["GCP_CREDENTIALS_TARGET_PATH"]).read_text())
        assert target["project_id"] == "proj-a"

    def test_delete_active_blocked_and_confirm_gate(self) -> None:
        self.client.post("/api/v1/review-admin/ai-accounts",
                         json={"name": "busy", "sa_json": SA_A})
        r0 = self.client.post("/api/v1/review-admin/ai-accounts/delete",
                              json={"name": "busy"})
        assert r0.status_code == 422  # no confirm
        import app.review.admin as am
        ok_checks = {"ok": True, "errors": [], "checks": {"token_mint": "ok"}}
        with mock.patch.object(am, "run_live_checks", lambda c, m: ok_checks):
            self.client.post("/api/v1/review-admin/ai-accounts/switch",
                             json={"name": "busy", "confirm": True})
        r = self.client.post("/api/v1/review-admin/ai-accounts/delete",
                             json={"name": "busy", "confirm": True})
        assert r.status_code == 409


if __name__ == "__main__":
    unittest.main()
