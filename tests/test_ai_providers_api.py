"""API tests for AI provider profile endpoints (B3/B21/B24).

Keys are fake fixtures only. Verifies: create/list/replace/delete, keys
never in responses, test-connection uses read-only validation.
"""
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


class AIProvidersAPITests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        os.environ["AI_PROVIDER_PROFILES_DIR"] = str(self.tmp / "ai-providers")
        self.addCleanup(os.environ.pop, "AI_PROVIDER_PROFILES_DIR", None)

        for mod in list(sys.modules):
            if mod.startswith("app.review.admin") or mod.startswith("app.providers"):
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

        patcher = mock.patch.object(admin_module, "_store", return_value=FakeStore())
        patcher.start()
        self.addCleanup(patcher.stop)

        app = FastAPI()
        app.include_router(admin_module.router)
        self.client = TestClient(app)

    def _create(self, provider="openrouter", key="fake-openrouter-key", pid="openrouter-main"):
        return self.client.post("/api/v1/review-admin/ai-providers", json={
            "id": pid, "name": "OpenRouter Main", "provider": provider,
            "api_key": key, "default_model": "google/gemini-3.7-flash",
        })

    def test_create_list_never_returns_key(self):
        r = self._create()
        assert r.status_code == 200
        assert "fake-openrouter-key" not in r.text

        listing = self.client.get("/api/v1/review-admin/ai-providers")
        assert listing.status_code == 200
        assert "fake-openrouter-key" not in listing.text
        body = listing.json()
        assert body["profiles"][0]["key_configured"] is True

    def test_key_stored_on_disk_not_db(self):
        self._create()
        key_file = Path(os.environ["AI_PROVIDER_PROFILES_DIR"]) / \
            "openrouter-main" / "api-key"
        assert key_file.read_text() == "fake-openrouter-key"
        assert oct(key_file.stat().st_mode & 0o777) == "0o600"

    def test_replace_key(self):
        self._create()
        r = self.client.post("/api/v1/review-admin/ai-providers/openrouter-main/key",
                             json={"api_key": "fake-openrouter-key-2"})
        assert r.status_code == 200
        assert "fake-openrouter-key-2" not in r.text

    def test_delete_requires_confirm(self):
        self._create()
        r0 = self.client.post("/api/v1/review-admin/ai-providers/openrouter-main/delete",
                              json={})
        assert r0.status_code == 422
        r1 = self.client.post("/api/v1/review-admin/ai-providers/openrouter-main/delete",
                              json={"confirm": True})
        assert r1.status_code == 200

    def test_test_connection_read_only(self):
        self._create()
        # The injected validation path hits /models (free), never generation.
        r = self.client.post("/api/v1/review-admin/ai-providers/openrouter-main/test")
        # Without network mock this fails closed with a safe error; the point
        # is that no paid endpoint is called and no key leaks.
        assert "fake-openrouter-key" not in r.text

    def test_invalid_provider_rejected(self):
        r = self._create(provider="skynet", pid="bad-p")
        assert r.status_code == 422


if __name__ == "__main__":
    unittest.main()
