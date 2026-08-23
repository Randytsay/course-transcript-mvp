"""AI / Vertex account profile manager (V2 - crash safe).

Layout under a *persistent host* directory (mounted read-write into the API
container only for this subtree):

    <accounts_dir>/
        profiles/<name>/credential.json   (0600, never returned)
        profiles/<name>/metadata.json     (name/project/location/bucket)
        staging/                          (temp dir for atomic swaps)
        active.json                       (commit pointer)
        ai-active.env                     (runtime env consumed by compose)

Activation is crash-safe via a two-phase protocol:

  Phase 1 (stage): write credential copy, env file and desired active doc
                   into ``staging/<generation>/`` with the generation id.
  Phase 2 (commit): atomically os.replace() each artifact into place, then
                   write ``active.json`` LAST as the single commit pointer.

Startup reconciliation (``reconcile()``) compares the committed pointer with
what is actually on disk. If they disagree (crash between replaces), the
system fails closed by re-applying the committed profile's artifacts from the
profile source of truth; if that is impossible the inconsistency is reported
and every runtime status becomes ``FAILED_CLOSED`` until an operator acts.

Runtime activation states:
    CONFIGURED       profile registered, never activated
    PENDING_RESTART  files switched; containers not yet verified
    ACTIVE           mounted credential + env match the selected profile
    FAILED_CLOSED    reconciliation detected inconsistency
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

SA_REQUIRED_FIELDS = ("client_email", "private_key", "project_id", "type")
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
DEFAULT_LOCATION = "global"
_SECRET_FIELDS = ("private_key", "private_key_id", "token")

RUNTIME_ENV_KEYS = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GCS_BUCKET")

# Billing / credit metadata — management hints only, never authoritative.
CREDIT_TYPES = ("google_ai_pro_monthly", "gcp_free_trial", "paid", "other", "unknown")
CREDIT_STATUSES = ("available", "low", "exhausted", "expired", "disabled", "unknown")
CREDIT_TYPE_LABELS = {
    "google_ai_pro_monthly": "Google AI Pro 每月額度",
    "gcp_free_trial": "Google Cloud 新戶試用",
    "paid": "付費帳戶",
    "other": "其他",
    "unknown": "未知",
}

DATE_FIELDS = ("trial_started_at", "trial_expires_at")


class AIAccountError(Exception):
    """Invalid profiles, malformed credentials or inconsistent state."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def sanitize(obj: Any) -> Any:
    """Recursively strip secret-bearing keys from responses / audit payloads."""
    if isinstance(obj, dict):
        return {
            k: sanitize(v)
            for k, v in obj.items()
            if not any(s in k.lower() for s in _SECRET_FIELDS)
        }
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


class AIAccountStore:
    CREDIT_TYPES = CREDIT_TYPES
    CREDIT_STATUSES = CREDIT_STATUSES
    CREDIT_TYPE_LABELS = CREDIT_TYPE_LABELS
    DATE_FIELDS = DATE_FIELDS

    def __init__(
        self,
        accounts_dir: Path,
        target_key_path: Path,
        *,
        preflight: Callable[..., dict[str, Any]] | None = None,
        audit_callback: Callable[..., None] | None = None,
    ):
        self.accounts_dir = Path(accounts_dir)
        self.target_key_path = Path(target_key_path)
        self.env_file = self.accounts_dir / "ai-active.env"
        self.active_file = self.accounts_dir / "active.json"
        self.staging_dir = self.accounts_dir / "staging"
        self._preflight = preflight
        self._audit = audit_callback or (lambda **_: None)

    # -- layout -----------------------------------------------------------

    @property
    def profiles_dir(self) -> Path:
        return self.accounts_dir / "profiles"

    def profile_dir(self, name: str) -> Path:
        if not SAFE_NAME.match(name or ""):
            raise AIAccountError("帳戶名稱只能使用英文、數字、點、底線與連字號")
        return self.profiles_dir / name

    # -- validation ---------------------------------------------------------

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
        tmp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex[:8]}")
        tmp.write_bytes(content.encode() if isinstance(content, str) else content)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)

    def save_profile(
        self, *, name: str, sa_json: dict[str, Any], location: str = DEFAULT_LOCATION,
        gcs_bucket: str = "", actor: str,
        credit_type: str = "unknown", billing_label: str = "", credit_note: str = "",
        credit_status: str = "unknown",
        trial_started_at: str = "", trial_expires_at: str = "",
    ) -> dict[str, Any]:
        pdir = self.profile_dir(name)
        identity = self.validate_credential(sa_json)
        existed = pdir.exists()
        pdir.mkdir(parents=True, exist_ok=True)
        self._write_atomic(pdir / "credential.json",
                           json.dumps(sa_json, indent=1), 0o600)

        if credit_type not in CREDIT_TYPES:
            raise AIAccountError(f"credit_type 必須是 {', '.join(CREDIT_TYPES)}")
        if credit_status not in CREDIT_STATUSES:
            raise AIAccountError(f"credit_status 必須是 {', '.join(CREDIT_STATUSES)}")
        dates = self._validate_dates(trial_started_at, trial_expires_at)

        meta = {
            "name": name,
            "client_email": identity["client_email"],
            "project_id": identity["project_id"],  # from JSON only — frontend can't override
            "location": (location.strip() or DEFAULT_LOCATION),
            "gcs_bucket": gcs_bucket.strip(),
            "uploaded_at": _now(),
            # billing / credit management metadata (non-secret, advisory only)
            "credit_type": credit_type,
            "billing_label": billing_label.strip(),
            "credit_note": credit_note.strip(),
            "credit_status": credit_status,      # owner-marked, NOT live billing
            "trial_started_at": dates[0],
            "trial_expires_at": dates[1],
        }
        self._write_atomic(pdir / "metadata.json", json.dumps(meta, ensure_ascii=False, indent=1))
        action = "ai_profile_replaced" if existed else "ai_profile_added"
        self._audit(actor=actor, action=action, entity_id=name,
                    payload=sanitize({"client_email": identity["client_email"],
                                      "project_id": identity["project_id"],
                                      "credit_type": credit_type,
                                      "billing_label": billing_label.strip()}))
        return {**meta, "replaced": existed}

    @staticmethod
    def _validate_dates(started: str, expires: str) -> tuple[str, str]:
        """Accept ISO dates only (YYYY-MM-DD); reject anything else."""
        from datetime import date

        def _ok(v: str) -> bool:
            if not v:
                return True
            try:
                date.fromisoformat(v)
                return True
            except ValueError:
                return False

        if not _ok(started) or not _ok(expires):
            raise AIAccountError("試用日期格式必須為 YYYY-MM-DD")
        return started.strip(), expires.strip()

    def update_credit_status(
        self, *, name: str, credit_status: str, actor: str,
    ) -> dict[str, Any]:
        """Owner marks available/low/exhausted/expired/disabled. Manual only."""
        if credit_status not in CREDIT_STATUSES:
            raise AIAccountError(f"credit_status 必須是 {', '.join(CREDIT_STATUSES)}")
        meta = self.load_metadata(name)
        previous = meta.get("credit_status", "unknown")
        meta["credit_status"] = credit_status
        meta_path = self.profile_dir(name) / "metadata.json"
        self._write_atomic(meta_path, json.dumps(meta, ensure_ascii=False, indent=1))
        self._audit(actor=actor, action="ai_profile_credit_status", entity_id=name,
                    payload={"previous": previous, "new": credit_status})
        return {"name": name, "credit_status": credit_status, "previous": previous}

    # -- credit / trial helpers ---------------------------------------------

    @staticmethod
    def trial_warning(meta: dict[str, Any]) -> dict[str, str | None]:
        """Return a display warning for gcp_free_trial profiles (30/7/expired)."""
        if meta.get("credit_type") != "gcp_free_trial":
            return {"level": None, "text": None}
        expires = meta.get("trial_expires_at") or ""
        if not expires:
            return {"level": "info", "text": "Google Cloud 新戶 US$300 試用額度（未記錄到期日）"}
        try:
            from datetime import date
            exp = date.fromisoformat(expires)
            delta = (exp - date.today()).days
        except ValueError:
            return {"level": "info", "text": "試用到期日格式異常，請確認 Billing Console"}
        if delta < 0:
            return {"level": "danger",
                    "text": "試用可能已結束，切換前請確認 Billing 狀態"}
        if delta <= 7:
            return {"level": "warn",
                    "text": f"試用即將到期（剩 {delta} 天）"}
        if delta <= 30:
            return {"level": "soft",
                    "text": f"試用將於 {delta} 天後到期"}
        return {"level": "ok", "text": f"試用尚餘 {delta} 天"}

    @staticmethod
    def sorted_profiles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Active first, then available, then AI Pro, then trial (soonest expiry),
        then unknown, then exhausted/expired/disabled. Never auto-switches."""
        from datetime import date

        def _rank(item: dict[str, Any]) -> tuple:
            if item.get("is_active"):
                return (0, 0)
            status = item.get("credit_status", "unknown")
            ctype = item.get("credit_type", "unknown")
            if status == "available":
                return (1, 0)
            if ctype == "google_ai_pro_monthly" and status != "exhausted":
                return (2, 0)
            if ctype == "gcp_free_trial" and status not in ("exhausted", "expired", "disabled"):
                exp = item.get("trial_expires_at") or "9999-12-31"
                try:
                    d = date.fromisoformat(exp).toordinal()
                except ValueError:
                    d = 9999 * 366
                return (3, d)
            if status in ("exhausted", "expired", "disabled"):
                return (5, 0)
            return (4, 0)

        return sorted(items, key=_rank)

    def load_metadata(self, name: str) -> dict[str, Any]:
        try:
            return json.loads((self.profile_dir(name) / "metadata.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AIAccountError(f"無法讀取帳戶資料 {name}: {exc}") from exc

    def read_credential(self, name: str) -> dict[str, Any]:
        try:
            return json.loads((self.profile_dir(name) / "credential.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AIAccountError(f"無法讀取憑證檔 {name}: {exc}") from exc

    def list_profiles(self) -> list[dict[str, Any]]:
        if not self.profiles_dir.is_dir():
            return []
        items = []
        for entry in sorted(self.profiles_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                meta: dict[str, Any] = self.load_metadata(entry.name)
                valid = True
            except AIAccountError:
                meta, valid = {"name": entry.name}, False
            meta["credential_valid"] = valid
            meta["is_active"] = self.get_active_name() == entry.name
            items.append(meta)
        return items

    # -- committed state ----------------------------------------------------

    def get_active_doc(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.active_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def get_active_name(self) -> str | None:
        doc = self.get_active_doc()
        return (doc or {}).get("name") or None

    def get_previous(self) -> str | None:
        doc = self.get_active_doc()
        return (doc or {}).get("previous") or None

    def _env_content(self, meta: dict[str, Any]) -> str:
        lines = ["# Managed by AI account manager - do not edit by hand",
                 f"# active_profile={meta['name']}"]
        lines.append(f"GOOGLE_CLOUD_PROJECT={meta['project_id']}")
        lines.append(f"GOOGLE_CLOUD_LOCATION={meta.get('location') or DEFAULT_LOCATION}")
        if meta.get("gcs_bucket"):
            lines.append(f"GCS_BUCKET={meta['gcs_bucket']}")
        return "\n".join(lines) + "\n"

    # -- crash-safe switching -------------------------------------------------

    def switch(
        self, *, name: str, actor: str, confirm: bool = False,
    ) -> dict[str, Any]:
        """Two-phase activation. confirm=False runs preflight only.

        There is deliberately NO client-side way to skip preflight: the only
        bypass is the internal test hook ``_activate_for_tests`` used by unit
        tests without google SDK credentials.
        """
        meta = self.load_metadata(name)  # raises 404-style error if unknown
        previous = self.get_active_name()
        same_project = bool(previous) and (
            self._safe_meta(previous).get("project_id") == meta["project_id"]
        )

        checks = self.run_preflight(name)
        result: dict[str, Any] = {
            "name": name, "previous": previous,
            "same_project_warning": same_project,
            "preflight": checks,
            "restart_required": ["api", "pipeline-worker"],
        }
        if not confirm:
            result["stage"] = "preflight"
            return result
        if not checks.get("ok"):
            raise AIAccountError(
                "Preflight 驗證未通過，已取消切換: "
                + "; ".join(str(e) for e in checks.get("errors") or [])
            )
        return self._activate(meta, previous=previous, actor=actor, result=result)

    def _activate(
        self, meta: dict[str, Any], *, previous: str | None, actor: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Stage everything, then commit pointer last. Crash-recoverable."""
        generation = uuid.uuid4().hex[:12]
        stage = self.staging_dir / generation
        stage.mkdir(parents=True, exist_ok=True)

        cred_src = self.profile_dir(meta["name"]) / "credential.json"
        cred_data = cred_src.read_bytes()
        cred_hash = __import__("hashlib").sha256(cred_data).hexdigest()

        desired = {
            "name": meta["name"], "previous": previous,
            "switched_at": _now(), "credential_sha256": cred_hash,
            "env_sha256": __import__("hashlib").sha256(
                self._env_content(meta).encode()).hexdigest(),
        }
        # Stage copies of every artifact first.
        self._write_atomic(stage / "credential.json", cred_data, 0o600)
        self._write_atomic(stage / "ai-active.env", self._env_content(meta), 0o600)
        self._write_atomic(stage / "desired-active.json",
                           json.dumps(desired, ensure_ascii=False, indent=1))

        rollback: list[tuple[Path, bytes | None]] = []

        def _replace(dst: Path, data: bytes, mode: int | None = None) -> None:
            rollback.append((dst, dst.read_bytes() if dst.exists() else None))
            self._write_atomic(dst, data, mode)

        try:
            # Order matters: credential & env BEFORE the commit pointer.
            _replace(self.target_key_path, cred_data, 0o600)
            _replace(self.env_file, self._env_content(meta).encode(), 0o600)
            # Commit marker LAST — its presence defines "activation complete".
            self._write_atomic(self.active_file,
                               json.dumps(desired, ensure_ascii=False, indent=1))
        except OSError:
            for dst, old in reversed(rollback):
                if old is None:
                    with contextlib_suppress():
                        dst.unlink(missing_ok=True)
                else:
                    with contextlib_suppress():
                        self._write_atomic(dst, old)
            raise AIAccountError("切換過程發生 I/O 錯誤，已還原所有變更")
        finally:
            shutil.rmtree(stage, ignore_errors=True)

        self._audit(actor=actor, action="ai_profile_switched", entity_id=meta["name"],
                    payload=sanitize({
                        "previous_profile": previous,
                        "previous_project_id": self._safe_meta(previous).get("project_id"),
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

    def _activate_for_tests(self, name: str, *, actor: str = "test") -> dict[str, Any]:
        """Internal test hook: activate without live preflight. Never exposed via API."""
        meta = self.load_metadata(name)
        return self._activate(meta, previous=self.get_active_name(), actor=actor, result={})

    # -- crash recovery -------------------------------------------------------

    def reconcile(self) -> dict[str, Any]:
        """Verify committed state matches on-disk artifacts; repair if possible.

        Called at startup (and by the health endpoint). Fail closed on any
        inconsistency that cannot be repaired from the profile directory.
        """
        report: dict[str, Any] = {"consistent": True, "repaired": False}
        doc = self.get_active_doc()
        if doc is None:
            # No activation ever committed: leftover artifacts are stale but harmless.
            return report
        name = doc.get("name")
        try:
            cred = self.read_credential(name)          # type: ignore[arg-type]
            meta = self.load_metadata(name)            # type: ignore[arg-type]
        except AIAccountError as exc:
            report.update({"consistent": False, "status": "FAILED_CLOSED",
                           "error": str(exc)})
            return report

        expected_cred_hash = _hash_bytes(json.dumps(cred, indent=1).encode())
        expected_env = self._env_content(meta)

        problems: list[str] = []
        mounted = _read_json_if_exists(self.target_key_path)
        if mounted is None:
            problems.append("掛載憑證缺失")
        elif _hash_bytes(json.dumps(mounted, indent=1).encode()) != expected_cred_hash \
                and mounted != json.loads(doc.get("_cred_raw", "null") or "null"):
            # hash mismatch tolerated when formatting differs; compare semantics instead
            semantically_equal = (
                mounted.get("client_email") == cred.get("client_email")
                and mounted.get("project_id") == cred.get("project_id")
            )
            if not semantically_equal:
                problems.append("掛載憑證與 committed profile 不一致")
        env_text = self.env_file.read_text("utf-8") if self.env_file.exists() else ""
        for line in expected_env.splitlines():
            if line.startswith("#"):
                continue
            if line not in env_text.splitlines():
                problems.append(f"env 缺少 {line.split('=')[0]}")

        if problems:
            # Auto-repair from profile source of truth (idempotent).
            self._activate(meta, previous=doc.get("previous"),
                           actor="startup-reconciliation",
                           result={"name": name})
            report.update({"consistent": False, "repaired": True,
                           "problems": problems, "status": "REPAIRED"})
            self._audit(actor="system", action="ai_state_reconciled",
                        entity_id=name, payload={"problems": problems})
        else:
            report["status"] = "ACTIVE"
        return report

    # -- runtime verification ---------------------------------------------------

    def runtime_status(self) -> dict[str, Any]:
        """CONFIGURED / PENDING_RESTART / ACTIVE / FAILED_CLOSED."""
        base: dict[str, Any] = {"status": "CONFIGURED"}
        doc = self.get_active_doc()
        if doc is None:
            return base
        recon = self.reconcile()
        if not recon.get("consistent") and not recon.get("repaired"):
            return {"status": "FAILED_CLOSED", **recon}
        meta = self.load_metadata(doc["name"])
        mounted = _read_json_if_exists(self.target_key_path)
        cred_match = bool(mounted) and (
            mounted.get("client_email") == meta.get("client_email")
            and mounted.get("project_id") == meta.get("project_id")
        )
        env_text = self.env_file.read_text("utf-8") if self.env_file.exists() else ""
        env_expected = self._env_content(meta)
        env_match = all(
            line in env_text.splitlines()
            for line in env_expected.splitlines() if not line.startswith("#")
        )
        if cred_match and env_match:
            base["status"] = "PENDING_RESTART"  # files ok; containers unverified
            # Container-level confirmation requires reading inside api itself;
            # the /health endpoint reports this via verify_runtime below.
        else:
            base.update({"status": "PENDING_RESTART",
                         "file_mismatch": {"credential": cred_match, "env": env_match}})
        base.update(sanitize({
            "active": doc["name"], "previous": doc.get("previous"),
            "project_id": meta.get("project_id"),
            "location": meta.get("location"),
            "bucket_configured": "yes" if meta.get("gcs_bucket") else "no",
            "reconciled_this_request": recon.get("repaired", False),
        }))
        return base

    def verify_runtime(self) -> dict[str, Any]:
        """Compare what THIS process sees (post-restart truth) vs selected profile.

        When the api container has been recreated it reads the new credential
        through its own GOOGLE_APPLICATION_CREDENTIALS mount; matching here
        upgrades status to ACTIVE.
        """
        doc = self.get_active_doc()
        if doc is None:
            return {"verified": False, "reason": "no_active_profile"}
        meta = self.load_metadata(doc["name"])
        mounted = _read_json_if_exists(self.target_key_path)
        checks = {
            "client_email": bool(mounted) and mounted.get("client_email") == meta.get("client_email"),
            "project_id": bool(mounted) and mounted.get("project_id") == meta.get("project_id"),
            "location": os.environ.get("GOOGLE_CLOUD_LOCATION", "") ==
                        (meta.get("location") or DEFAULT_LOCATION),
            "bucket": (not meta.get("gcs_bucket"))
                      or os.environ.get("GCS_BUCKET", "") == meta.get("gcs_bucket"),
        }
        project_env_ok = os.environ.get("GOOGLE_CLOUD_PROJECT", "") == meta.get("project_id")
        verified = all(checks.values()) and project_env_ok
        return {
            "verified": verified,
            "checks": checks | {"google_cloud_project_env": project_env_ok},
            "expected": sanitize({k: meta.get(k) for k in
                                  ("name", "client_email", "project_id", "location", "gcs_bucket")}),
        }

    # -- preflight ---------------------------------------------------------

    def run_preflight(self, name: str) -> dict[str, Any]:
        errors: list[str] = []
        checks: dict[str, Any] = {}
        try:
            cred = self.read_credential(name)
            identity = self.validate_credential(cred)
            checks["credential"] = "ok"
        except AIAccountError as exc:
            errors.append(str(exc))
            return {"ok": False, "errors": errors}
        try:
            meta = self.load_metadata(name)
        except AIAccountError as exc:
            errors.append(str(exc))
            return {"ok": False, "errors": errors}

        # Structural project mismatch always fails.
        if cred.get("project_id") != meta.get("project_id"):
            errors.append(
                f"project mismatch: credential={cred.get('project_id')} "
                f"metadata={meta.get('project_id')}"
            )
        checks["location"] = meta.get("location", DEFAULT_LOCATION)

        if self._preflight is not None:
            live = self._preflight(cred, meta)
            checks.update(live.get("checks") or {})
            errors.extend(live.get("errors") or [])
        return {"ok": not errors, "errors": errors, "checks": checks}

    # -- deletion -------------------------------------------------------------

    def delete_profile(self, *, name: str, actor: str) -> dict[str, Any]:
        pdir = self.profile_dir(name)
        if not pdir.exists():
            raise AIAccountError("找不到這個帳戶")
        if self.get_active_name() == name:
            raise AIAccountError("此帳戶目前使用中，請先切換到其他帳戶再刪除")
        shutil.rmtree(pdir)
        self._audit(actor=actor, action="ai_profile_deleted", entity_id=name, payload={})
        return {"name": name, "deleted": True}

    def rollback(self, *, actor: str) -> dict[str, Any]:
        previous = self.get_previous()
        if not previous:
            raise AIAccountError("沒有可回滾的前一個帳戶")
        meta = self.load_metadata(previous)
        result = self._activate(meta, previous=self.get_active_name(),
                                actor=f"{actor} (rollback)", result={"name": previous})
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


# -- helpers ------------------------------------------------------------------

def contextlib_suppress():
    import contextlib
    return contextlib.suppress(OSError)


def _hash_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
