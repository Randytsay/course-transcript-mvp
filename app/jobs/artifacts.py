"""Flexible artifact evidence for selected-output pipelines."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_evidence(job_dir: Path) -> list[dict[str, Any]]:
    manifest_path = job_dir / "export-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("缺少 export-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("export manifest 沒有有效輸出")
    evidence: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            raise RuntimeError("export manifest artifact 格式錯誤")
        name = str(item.get("name") or "")
        if not name or Path(name).name != name:
            raise RuntimeError("export manifest artifact 名稱不安全")
        path = job_dir / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"缺少必要輸出：{name}")
        actual = _sha256(path)
        expected = str(item.get("sha256") or "")
        if expected and actual != expected:
            raise RuntimeError(f"輸出雜湊不一致：{name}")
        evidence.append(
            {
                "name": name,
                "size_bytes": path.stat().st_size,
                "sha256": actual,
                "source": item.get("source", "selected export manifest"),
                "public": bool(item.get("public")),
                "output_format": item.get("output_format"),
            }
        )
    return evidence


def install_artifact_patch(worker_module: Any) -> None:
    worker_module._artifact_evidence = artifact_evidence
