"""Provider registry + secure API-key profile store.

Profiles live under a dedicated host subtree (mounted RW to api, RO to
pipeline-worker):

    <profiles_dir>/<profile_id>/api-key      (0600)
    <profiles_dir>/<profile_id>/metadata.json

Keys NEVER enter SQLite, logs, audit payloads or API responses.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import ProviderError, ProviderId  # noqa: E402
from .vertex import VertexCorrectionProvider
from .openrouter import OpenRouterCorrectionProvider
from .minimax import MiniMaxCorrectionProvider


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
PROVIDER_CLASSES = {
    ProviderId.VERTEX: VertexCorrectionProvider,
    ProviderId.OPENROUTER: OpenRouterCorrectionProvider,
    ProviderId.MINIMAX: MiniMaxCorrectionProvider,
}

# Extended secret redaction for provider payloads.
_SECRET_KEY_PARTS = ("api_key", "apikey", "authorization", "bearer",
                     "secret", "token", "private_key")


def redact(obj: Any) -> Any:
    """Recursively remove credential-bearing keys (extended set)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(part in kl for part in _SECRET_KEY_PARTS):
                continue
            out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class AIProviderProfileStore:
    def __init__(self, profiles_dir: Path):
        self.profiles_dir = Path(profiles_dir)

    def profile_dir(self, profile_id: str) -> Path:
        if not SAFE_ID.match(profile_id or ""):
            raise ProviderError("unknown", "設定檔 ID 只能用小寫英文、數字與連字號")
        return self.profiles_dir / profile_id

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _write_atomic(self, path: Path, content: str, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)

    # -- CRUD ---------------------------------------------------------------

    def _ensure_private_dir(self, path: Path) -> None:
        """Normalize directory mode to 0700 (also fixes pre-existing dirs)."""
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    def create(self, *, profile_id: str, name: str, provider: str,
               api_key: str, default_model: str) -> dict[str, Any]:
        if provider not in PROVIDER_CLASSES:
            raise ProviderError("unknown", f"未知的 provider: {provider}")
        pdir = self.profile_dir(profile_id)
        existed = pdir.exists()
        self._ensure_private_dir(pdir)
        self._ensure_private_dir(self.profiles_dir)
        self._write_atomic(pdir / "api-key", api_key, 0o600)
        meta = {
            "id": profile_id,
            "name": name.strip(),
            "provider": provider,
            "default_model": default_model.strip(),
            "created_at": self._now(),
            "updated_at": self._now(),
            "last_validated_at": None,
            "validation_status": "UNKNOWN",
        }
        self._write_atomic(pdir / "metadata.json",
                           json.dumps(meta, ensure_ascii=False, indent=1))
        return {**meta, "replaced": existed,
                "key_configured": True}  # never the key itself

    def replace_key(self, profile_id: str, *, api_key: str) -> dict[str, Any]:
        pdir = self.profile_dir(profile_id)
        if not pdir.exists():
            raise ProviderError("unknown", "找不到這個供應商設定檔")
        self._write_atomic(pdir / "api-key", api_key, 0o600)
        meta = self.load_metadata(profile_id)
        meta["updated_at"] = self._now()
        meta["validation_status"] = "UNKNOWN"
        self._write_atomic(pdir / "metadata.json",
                           json.dumps(meta, ensure_ascii=False, indent=1))
        return {"id": profile_id, "key_replaced": True}

    def delete(self, profile_id: str) -> dict[str, Any]:
        import shutil
        pdir = self.profile_dir(profile_id)
        if not pdir.exists():
            raise ProviderError("unknown", "找不到這個供應商設定檔")
        shutil.rmtree(pdir)
        return {"id": profile_id, "deleted": True}

    def load_metadata(self, profile_id: str) -> dict[str, Any]:
        try:
            return json.loads(
                (self.profile_dir(profile_id) / "metadata.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderError("unknown", f"無法讀取設定檔 {profile_id}: {exc}") from exc

    def read_key(self, profile_id: str) -> str:
        try:
            key = (self.profile_dir(profile_id) / "api-key").read_text("utf-8").strip()
        except OSError as exc:
            raise ProviderError("auth", f"無法讀取 API key {profile_id}") from exc
        if not key:
            raise ProviderError("auth", f"API key 空白: {profile_id}")
        return key

    def list_profiles(self) -> list[dict[str, Any]]:
        if not self.profiles_dir.is_dir():
            return []
        items = []
        for entry in sorted(self.profiles_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                meta = self.load_metadata(entry.name)
                meta["key_configured"] = (entry / "api-key").exists()
            except ProviderError:
                meta = {"id": entry.name, "key_configured": False}
            items.append(redact(meta))
        return items

    def mark_validated(self, profile_id: str, status: str) -> None:
        meta = self.load_metadata(profile_id)
        meta["last_validated_at"] = self._now()
        meta["validation_status"] = status
        self._write_atomic(self.profile_dir(profile_id) / "metadata.json",
                           json.dumps(meta, ensure_ascii=False, indent=1))

    # -- client factory --------------------------------------------------------

    def build_client(self, profile_id: str, *, model: str | None = None,
                     http=None):
        meta = self.load_metadata(profile_id)
        cls = PROVIDER_CLASSES.get(meta.get("provider"))
        if cls is None:
            raise ProviderError("unknown", f"未知 provider: {meta.get('provider')}")
        if cls is VertexCorrectionProvider:
            return cls(model=model or meta.get("default_model") or cls.default_model)
        return cls(api_key=self.read_key(profile_id),
                   model=model or meta.get("default_model"),
                   http=http)


def fingerprint(key: str) -> str:
    """Short non-reversible display fingerprint for the UI."""
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:8]
