"""AI account (GCP service account) management for the owner admin UI.

Credentials live under ``$AI_ACCOUNTS_DIR`` (host path, root-only). Each
account is stored as ``<safe_name>.json``; the currently active one is
recorded in ``<active-file>``. Switching copies the chosen key over the
compose-mounted ``gcp-sa.json`` path so a worker restart picks it up.

Security notes:
- Private keys are NEVER returned by any endpoint (only metadata).
- Uploads are validated as parseable service-account JSON with required fields.
- All mutations write to review_admin_audit for traceability.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

SA_REQUIRED_FIELDS = ("client_email", "private_key", "project_id", "type")
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class AIAccountError(Exception):
    """Raised for invalid names, malformed keys or missing files."""


class AIAccountStore:
    """Filesystem-backed registry of GCP service-account keys."""

    def __init__(
        self,
        accounts_dir: Path,
        active_file: Path,
        target_key_path: Path,
        audit_callback=None,
    ):
        self.accounts_dir = Path(accounts_dir)
        self.active_file = Path(active_file)
        self.target_key_path = Path(target_key_path)
        self._audit = audit_callback or (lambda **_: None)

    # -- helpers ---------------------------------------------------------

    def _path(self, name: str) -> Path:
        if not SAFE_NAME.match(name or ""):
            raise AIAccountError("帳戶名稱只能使用英文、數字、點、底線與連字號")
        return self.accounts_dir / f"{name}.json"

    def validate_sa(self, payload: dict[str, Any]) -> str:
        missing = [f for f in SA_REQUIRED_FIELDS if not payload.get(f)]
        if missing:
            raise AIAccountError(f"服務帳戶 JSON 缺少必要欄位: {', '.join(missing)}")
        if payload.get("type") != "service_account":
            raise AIAccountError("type 必須為 service_account")
        return str(payload["client_email"])

    def _read_meta(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AIAccountError(f"無法讀取憑證檔 {path.name}: {exc}") from exc
        client_email = self.validate_sa(data)
        stat = path.stat()
        return {
            "name": path.stem,
            "client_email": client_email,
            "project_id": str(data.get("project_id", "")),
            "uploaded_at": datetime_iso(stat.st_mtime),
            "size_bytes": stat.st_size,
        }

    # -- operations ------------------------------------------------------

    def list_accounts(self) -> list[dict[str, Any]]:
        if not self.accounts_dir.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.accounts_dir.glob("*.json")):
            try:
                meta = self._read_meta(path)
            except AIAccountError:
                meta = {"name": path.stem, "invalid": True}
            meta["is_active"] = self.get_active() == path.stem
            items.append(meta)
        return items

    def get_active(self) -> str | None:
        try:
            name = self.active_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return name or None

    def add_account(self, *, name: str, sa_json: dict[str, Any], actor: str) -> dict[str, Any]:
        safe_path = self._path(name)  # validates name
        client_email = self.validate_sa(sa_json)
        existed = safe_path.exists()
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        tmp = safe_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sa_json, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, safe_path)
        action = "ai_account_replaced" if existed else "ai_account_added"
        self._audit(actor=actor, action=action, entity_type="ai_account",
                    entity_id=safe_path.stem, payload={"client_email": client_email})
        return {"name": safe_path.stem, "client_email": client_email, "replaced": existed}

    def delete_account(self, *, name: str, actor: str) -> dict[str, Any]:
        safe_path = self._path(name)
        if not safe_path.exists():
            raise AIAccountError("找不到這個帳戶")
        was_active = self.get_active() == name
        if was_active and self.target_key_path.exists():
            raise AIAccountError("此帳戶目前使用中，請先切換到其他帳戶再刪除")
        safe_path.unlink()
        self._audit(actor=actor, action="ai_account_deleted", entity_type="ai_account",
                    entity_id=name, payload={})
        return {"name": name, "deleted": True}

    def switch_active(self, *, name: str, actor: str) -> dict[str, Any]:
        safe_path = self._path(name)
        if not safe_path.exists():
            raise AIAccountError("找不到這個帳戶")
        previous = self.get_active()
        # Atomic copy onto the compose-mounted gcp-sa.json.
        self.target_key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.target_key_path.with_suffix(".json.tmp")
        shutil.copyfile(safe_path, tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.target_key_path)
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self.active_file.write_text(safe_path.stem, encoding="utf-8")
        meta = self._read_meta(safe_path)
        self._audit(actor=actor, action="ai_account_switched", entity_type="ai_account",
                    entity_id=safe_path.stem,
                    payload={"previous": previous, "client_email": meta["client_email"]})
        return {"name": safe_path.stem, "previous": previous,
                "client_email": meta["client_email"], "restart_required": True}

    def verify_active(self) -> dict[str, Any]:
        """Check the file mounted into containers parses as a service account."""
        try:
            data = json.loads(self.target_key_path.read_text(encoding="utf-8"))
            email = self.validate_sa(data)
            return {"ok": True, "client_email": email}
        except (OSError, json.JSONDecodeError, AIAccountError) as exc:
            return {"ok": False, "error": str(exc)}


def datetime_iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
