from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


def _make_client(tmp: Path):
    """Import admin router with AI account paths pointed at a temp dir."""
    for mod in list(sys.modules):
        if mod.startswith("app.review.admin") or mod == "app.api":
            del sys.modules[mod]
    os.environ["AI_ACCOUNTS_DIR"] = str(tmp / "ai-accounts")
    os.environ["GCP_CREDENTIALS_TARGET_PATH"] = str(tmp / "mounted" / "gcp-sa.json")
    import app.review.admin as admin_module

    importlib.reload(admin_module)
    return admin_module, admin_module.router


SA_OK = {
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
        self.addCleanup(os.environ.pop, "AI_ACCOUNTS_DIR", None)
        self.addCleanup(os.environ.pop, "GCP_CREDENTIALS_TARGET_PATH", None)

        # Stub the ReviewAdminStore used by _store() to avoid DB dependency.
        from app.review import admin as fresh
        from app.review.admin_store import ReviewAdminStore

        class FakeAudit:
            from contextlib import nullcontext

            def transaction(self):
                import contextlib

                class _Conn:
                    def execute(self, *_a, **_k):
                        return None

                @contextlib.contextmanager
                def cm():
                    yield _Conn()

                return cm()

        self.admin_mod, router = _make_client(self.tmp)
        patcher = mock.patch.object(self.admin_mod, "_store", return_value=FakeAudit())
        patcher.start()
        self.addCleanup(patcher.stop)
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_list_empty_accounts(self) -> None:
        r = self.client.get("/api/v1/review-admin/ai-accounts")
        assert r.status_code == 200
        body = r.json()
        assert body["accounts"] == []
        assert body["verify"]["ok"] is False  # target key not mounted yet

    def test_add_and_switch_roundtrip(self) -> None:
        r = self.client.post(
            "/api/v1/review-admin/ai-accounts",
            json={"name": "acct-x", "sa_json": SA_OK},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "acct-x"

        target = Path(os.environ["GCP_CREDENTIALS_TARGET_PATH"])
        assert not target.exists()

        r2 = self.client.post(
            "/api/v1/review-admin/ai-accounts/switch",
            json={"name": "acct-x", "confirm": True},
        )
        assert r2.status_code == 200
        assert json.loads(target.read_text())["client_email"] == SA_OK["client_email"]

        listing = self.client.get("/api/v1/review-admin/ai-accounts").json()
        assert listing["active"] == "acct-x"
        assert listing["accounts"][0]["is_active"] is True
        dumped = json.dumps(listing)
        assert "BEGIN PRIVATE KEY" not in dumped

    def test_add_rejects_bad_sa(self) -> None:
        r = self.client.post(
            "/api/v1/review-admin/ai-accounts",
            json={"name": "bad", "sa_json": {"type": "nope"}},
        )
        assert r.status_code == 422
        assert "缺少必要欄位" in r.json()["detail"]

    def test_switch_requires_confirm(self) -> None:
        self.client.post("/api/v1/review-admin/ai-accounts", json={"name": "y", "sa_json": SA_OK})
        r = self.client.post("/api/v1/review-admin/ai-accounts/switch", json={"name": "y"})
        assert r.status_code == 422

    def test_delete_active_blocked(self) -> None:
        self.client.post("/api/v1/review-admin/ai-accounts", json={"name": "z", "sa_json": SA_OK})
        self.client.post(
            "/api/v1/review-admin/ai-accounts/switch",
            json={"name": "z", "confirm": True},
        )
        r = self.client.post(
            "/api/v1/review-admin/ai-accounts/delete",
            json={"name": "z", "confirm": True},
        )
        assert r.status_code == 409


if __name__ == "__main__":
    unittest.main()
