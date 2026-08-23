"""AI / Vertex account profile manager.

A *profile* bundles everything the pipeline and API need to talk to one
Vertex configuration:

    <accounts_dir>/<profile_name>/
        credential.json   (service-account key, 0600, never returned)
        metadata.json     (name / client_email / project_id / location / bucket)

The currently selected profile is recorded in two places:

    active.json       (inside accounts_dir; source of truth for the manager)
    ai-active.env     (protected runtime env file consumed by docker compose
                       as an env_file so recreated containers pick up the new
                       GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION /
                       GCS_BUCKET without editing the global .env)

Switching atomically:
1. copies credential.json over the mounted gcp-sa.json target
2. rewrites ai-active.env with non-secret runtime metadata
3. writes active.json

All steps stage into temp files first and os.replace() at the end so a crash
can never leave a half-switched state. Rollback restores all three artifacts.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SA_REQUIRED_FIELDS = ("client_email", "private_key", "project_id", "type")
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
DEFAULT_LOCATION = "global"
# Never allowed to appear in any API response or audit payload.
_SECRET_FIELDS = ("private_key", "private_key_id", "client_x509_cert_url", "token")

RUNTIME_ENV_KEYS = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GCS_BUCKET")


class AIAccountError(Exception):
    """Raised for invalid profiles, malformed credentials or missing files."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def sanitize(obj: Any) -> Any:
    """Recursively strip secret-bearing keys from payloads bound for responses/audit."""
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if any(s in k.lower() for s in _SECRET_FIELDS) else sanitize(v))
            for k, v in obj.items()
            if not any(s in k.lower() for s in _SECRET_FIELDS)
        }
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


class AIAccountStore:
    def __init__(
        self,
        accounts_dir: Path,
        env_file: Path,
        target_key_path: Path,
        *,
        preflight=None,
        audit_callback=None,
    ):
        self.accounts_dir = Path(accounts_dir)
        self.env_file = Path(env_file)
        self.target_key_path = Path(target_key_path)
        self._preflight = preflight  # callable(profile) -> {"ok": bool, ...}
        self._audit = audit_callback or (lambda **_: None)

    # -- paths -----------------------------------------------------------

    def profile_dir(self, name: str) -> Path:
        if not SAFE_NAME.match(name or ""):
            raise AIAccountError("帳戶名稱只能使用英文、數字、點、底線與連字號")
        return self.accounts_dir / name

    @property
    def active_file(self) -> Path:
        return self.accounts_dir / "active.json"

    # -- validation ------------------------------------------------------

    @staticmethod
    def validate_credential(payload: dict[str, Any]) -> dict[str, str]:
        missing = [f for f in SA_REQUIRED_FIELDS if not payload.get(f)]
        if missing:
            raise AIAccountError(f"服務帳戶 JSON 缺少必要欄位: {', '.join(missing)}")
        if payload.get("type") != "service_account":
            raise AIAccountError("type 必須為 service_account")
        return {
            "client_email": str(payload["client_email"]),
            "project_id": str(payload["project_id"]),
        }

    # -- persistence -----------------------------------------------------

    def _write_atomic(self, path: Path, content: bytes | str, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content.encode() if isinstance(content, str) else content)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)

    def save_profile(
        self,
        *,
        name: str,
        sa_json: dict[str, Any],
        location: str = DEFAULT_LOCATION,
        gcs_bucket: str = "",
        actor: str,
    ) -> dict[str, Any]:
        pdir = self.profile_dir(name)
        identity = self.validate_credential(sa_json)
        existed = pdir.exists()
        pdir.mkdir(parents=True, exist_ok=True)
        self._write_atomic(pdir / "credential.json", json.dumps(sa_json, indent=1), 0o600)
        meta = {
            "name": name,
            "client_email": identity["client_email"],
            "project_id": identity["project_id"],  # from JSON only — never frontend
            "location": (location.strip() or DEFAULT_LOCATION),
            "gcs_bucket": gcs_bucket.strip(),
            "uploaded_at": _now(),
        }
        self._write_atomic(pdir / "metadata.json", json.dumps(meta, ensure_ascii=False, indent=1))
        action = "ai_profile_replaced" if existed else "ai_profile_added"
        self._audit(actor=actor, action=action, entity_id=name,
                    payload=sanitize({"client_email": identity["client_email"],
                                      "project_id": identity["project_id"]}))
        return {**meta, "replaced": existed}

    def load_metadata(self, name: str) -> dict[str, Any]:
        path = self.profile_dir(name) / "metadata.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AIAccountError(f"無法讀取帳戶資料 {name}: {exc}") from exc

    def read_credential(self, name: str) -> dict[str, Any]:
        path = self.profile_dir(name) / "credential.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AIAccountError(f"無法讀取憑證檔 {name}: {exc}") from exc

    def list_profiles(self) -> list[dict[str, Any]]:
        if not self.accounts_dir.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for entry in sorted(self.accounts_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                meta = self.load_metadata(entry.name)
                cred_ok = True
            except AIAccountError:
                meta = {"name": entry.name}
                cred_ok = False
            meta["credential_valid"] = cred_ok
            meta["is_active"] = self.get_active() == entry.name
            items.append(meta)
        return items

    # -- active state ------------------------------------------------------

    def get_active(self) -> str | None:
        try:
            data = json.loads(self.active_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        name = data.get("name")
        return name or None

    def get_previous(self) -> str | None:
        try:
            data = json.loads(self.active_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data.get("previous") or None

    def _runtime_env_content(self, meta: dict[str, Any]) -> str:
        lines = [
            "# Managed by AI account manager - do not edit by hand",
            f"# active_profile={meta['name']}",
        ]
        lines.append(f"GOOGLE_CLOUD_PROJECT={meta['project_id']}")
        lines.append(f"GOOGLE_CLOUD_LOCATION={meta.get('location') or DEFAULT_LOCATION}")
        if meta.get("gcs_bucket"):
            lines.append(f"GCS_BUCKET={meta['gcs_bucket']}")
        return "\n".join(lines) + "\n"

    # -- switching ---------------------------------------------------------

    def switch(
        self,
        *,
        name: str,
        actor: str,
        confirm: bool = False,
        skip_preflight: bool = False,
    ) -> dict[str, Any]:
        pdir = self.profile_dir(name)
        if not (pdir / "metadata.json").exists():
            raise AIAccountError("找不到這個帳戶")
        meta = self.load_metadata(name)
        previous = self.get_active()
        same_project = bool(previous) and (
            self._safe_meta(previous).get("project_id") == meta["project_id"]
        )

        result: dict[str, Any] = {
            "name": name,
            "previous": previous,
            "same_project_warning": same_project,
            "restart_required": ["api", "pipeline-worker"],
        }
        if not confirm:
            # Preflight-only pass: validate everything but change nothing.
            checks = self.run_preflight(name) if not skip_preflight else {"ok": True}
            result.update({"stage": "preflight", "preflight": checks})
            return result

        checks = self.run_preflight(name) if not skip_preflight else {"ok": True}
        if not checks.get("ok"):
            raw_errors = checks.get("errors") or []
            raise AIAccountError(
                "Preflight 驗證未通過，已取消切換: "
                + "; ".join(str(e) for e in raw_errors)
            )

        # ---- atomic multi-file switch (staged then applied) ----
        staged: list[tuple[Path, bytes | str, int | None]] = []
        rollback_data: list[tuple[Path, bytes | None, int | None]] = []

        cred_src = pdir / "credential.json"
        staged.append((self.target_key_path, cred_src.read_bytes(), 0o600))
        staged.append((self.env_file, self._runtime_env_content(meta), 0o600))
        active_doc = json.dumps({"name": name, "previous": previous, "switched_at": _now()},
                                ensure_ascii=False, indent=1)
        staged.append((self.active_file, active_doc, 0o600))

        applied: list[Path] = []
        try:
            for path, content, mode in staged:
                rollback_data.append((
                    path,
                    path.read_bytes() if path.exists() else None,
                    (path.stat().st_mode & 0o777) if path.exists() else mode,
                ))
                self._write_atomic(path, content, mode)
                applied.append(path)
        except OSError:
            # Roll back every already-applied artifact; partial switch impossible.
            for path, old, old_mode in reversed(rollback_data[: len(applied)]):
                if old is None:
                    path.unlink(missing_ok=True)
                elif old_mode is not None:
                    self._write_atomic(path, old, old_mode)
                else:
                    self._write_atomic(path, old)
            raise AIAccountError("切換過程發生 I/O 錯誤，已還原所有變更")

        self._audit(actor=actor, action="ai_profile_switched", entity_id=name,
                    payload=sanitize({
                        "previous_profile": previous,
                        "previous_project_id": (self._safe_meta(previous).get("project_id")
                                                if previous else None),
                        "new_project_id": meta["project_id"],
                        "new_location": meta.get("location"),
                        "gcs_bucket": meta.get("gcs_bucket") or None,
                    }))
        result.update({
            "stage": "switched",
            "client_email": meta["client_email"],
            "project_id": meta["project_id"],
            "location": meta.get("location"),
            "gcs_bucket": meta.get("gcs_bucket") or None,
            "rollback_available": bool(previous),
        })
        return result

    def rollback(self, *, actor: str) -> dict[str, Any]:
        previous = self.get_previous()
        if not previous:
            raise AIAccountError("沒有可回滾的前一個帳戶")
        result = self.switch(name=previous, actor=f"{actor} (rollback)",
                             confirm=True, skip_preflight=False)
        self._audit(actor=actor, action="ai_profile_rolled_back", entity_id=previous,
                    payload={"to_previous": previous})
        result["rolled_back"] = True
        return result

    def _safe_meta(self, name: str | None) -> dict[str, Any]:
        if not name:
            return {}
        try:
            return self.load_metadata(name)
        except AIAccountError:
            return {}

    # -- preflight ---------------------------------------------------------

    def run_preflight(self, name: str) -> dict[str, Any]:
        errors: list[str] = []
        checks: dict[str, Any] = {}
        pdir = self.profile_dir(name)
        cred_path = pdir / "credential.json"
        if not cred_path.exists():
            errors.append("credential.json 不存在")
        else:
            try:
                cred = json.loads(cred_path.read_text(encoding="utf-8"))
                identity = self.validate_credential(cred)
                checks["credential"] = "ok"
            except (json.JSONDecodeError, AIAccountError) as exc:
                errors.append(str(exc))
                cred = None

        meta_ok = True
        try:
            meta = self.load_metadata(name)
            checks["location"] = meta.get("location", DEFAULT_LOCATION)
            checks["bucket_configured"] = "yes" if meta.get("gcs_bucket") else "no"
        except AIAccountError as exc:
            meta_ok = False
            errors.append(str(exc))

        # Optional live check via injected preflight callable (read-only).
        if cred is not None and meta_ok and self._preflight is not None:
            live = self._preflight(cred, meta)
            checks.update(live)
            if not live.get("ok"):
                errors.extend(live.get("errors", []))

        return {"ok": not errors, "checks": checks, "errors": errors}

    # -- deletion ----------------------------------------------------------

    def delete_profile(self, *, name: str, actor: str) -> dict[str, Any]:
        pdir = self.profile_dir(name)
        if not pdir.exists():
            raise AIAccountError("找不到這個帳戶")
        if self.get_active() == name:
            raise AIAccountError("此帳戶目前使用中，請先切換到其他帳戶再刪除")
        shutil.rmtree(pdir)
        self._audit(actor=actor, action="ai_profile_deleted", entity_id=name, payload={})
        return {"name": name, "deleted": True}

    # -- health --------------------------------------------------------------

    def verify_runtime(self) -> dict[str, Any]:
        """Check the mounted credential parses and the env file matches active."""
        report: dict[str, Any] = {"credential_ok": False, "env_matches": False}
        try:
            data = json.loads(self.target_key_path.read_text(encoding="utf-8"))
            identity = self.validate_credential(data)
            report["credential_ok"] = True
            report["mounted_client_email"] = identity["client_email"]
            report["mounted_project_id"] = identity["project_id"]
        except (OSError, json.JSONDecodeError, AIAccountError) as exc:
            report["credential_error"] = str(exc)
        try:
            active = self.get_active()
            if active:
                meta = self.load_metadata(active)
                env_text = self.env_file.read_text(encoding="utf-8")
                expected = self._runtime_env_content(meta)
                report["env_matches"] = env_text.strip() in expected or expected.strip() in env_text
        except (OSError, AIAccountError):
            pass
        return report
