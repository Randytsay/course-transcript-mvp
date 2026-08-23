from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.review.ai_accounts_store import AIAccountError, AIAccountStore

SA_OK = {
    "type": "service_account",
    "project_id": "proj-a",
    "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
    "client_email": "sa-a@proj-a.iam.gserviceaccount.com",
}


class AIAccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.dir = self.base / "ai-accounts"
        self.target = self.base / "mounted" / "gcp-sa.json"
        self.target.parent.mkdir(parents=True)
        self.audit: list[tuple[str, str]] = []
        self.store = AIAccountStore(
            accounts_dir=self.dir,
            active_file=self.dir / ".active",
            target_key_path=self.target,
            audit_callback=lambda *, actor, action, entity_type, entity_id, payload=None: (
                self.audit.append((actor, action))
            ),
        )

    # -- validation ------------------------------------------------------

    def test_rejects_invalid_name(self) -> None:
        for bad in ("../evil", "", "a/b", ".hidden", "x" * 100):
            with self.assertRaises(AIAccountError):
                self.store.add_account(name=bad, sa_json=SA_OK, actor="owner")

    def test_rejects_malformed_sa(self) -> None:
        bad = {"type": "service_account"}  # missing fields
        with self.assertRaises(AIAccountError):
            self.store.add_account(name="acc1", sa_json=bad, actor="owner")

    def test_add_account_writes_600_and_audits(self) -> None:
        result = self.store.add_account(name="acct-b", sa_json=SA_OK, actor="owner")
        assert result["client_email"] == SA_OK["client_email"]
        assert not result["replaced"]
        path = self.dir / "acct-b.json"
        assert path.exists()
        assert (path.stat().st_mode & 0o777) == 0o600
        assert ("owner", "ai_account_added") in self.audit
        # replacing audits differently
        self.store.add_account(name="acct-b", sa_json=SA_OK, actor="owner")
        assert ("owner", "ai_account_replaced") in self.audit

    # -- switching ---------------------------------------------------------

    def test_switch_copies_key_and_records_active(self) -> None:
        self.store.add_account(name="acct-c", sa_json=SA_OK, actor="owner")
        result = self.store.switch_active(name="acct-c", actor="owner")
        assert result["restart_required"] is True
        mounted = json.loads(self.target.read_text())
        assert mounted["client_email"] == SA_OK["client_email"]
        assert (self.target.stat().st_mode & 0o777) == 0o600
        assert self.store.get_active() == "acct-c"
        assert ("owner", "ai_account_switched") in self.audit

    def test_switch_missing_account_raises(self) -> None:
        with self.assertRaises(AIAccountError):
            self.store.switch_active(name="ghost", actor="owner")

    # -- deletion ----------------------------------------------------------

    def test_delete_active_account_blocked(self) -> None:
        self.store.add_account(name="in-use", sa_json=SA_OK, actor="owner")
        self.store.switch_active(name="in-use", actor="owner")
        with self.assertRaises(AIAccountError):
            self.store.delete_account(name="in-use", actor="owner")

    def test_delete_inactive_account_ok(self) -> None:
        self.store.add_account(name="gone", sa_json=SA_OK, actor="owner")
        result = self.store.delete_account(name="gone", actor="owner")
        assert result["deleted"] is True
        assert not (self.dir / "gone.json").exists()

    # -- listing -----------------------------------------------------------

    def test_list_never_exposes_private_key(self) -> None:
        self.store.add_account(name="secret-one", sa_json=SA_OK, actor="owner")
        items = self.store.list_accounts()
        dumped = json.dumps(items)
        assert "private_key" not in dumped
        assert "BEGIN PRIVATE KEY" not in dumped
        meta = items[0]
        assert meta["name"] == "secret-one"
        assert meta["project_id"] == "proj-a"
        assert isinstance(meta["is_active"], bool)

    def test_verify_active_reports_missing_target(self) -> None:
        report = self.store.verify_active()
        assert report["ok"] is False


if __name__ == "__main__":
    unittest.main()
