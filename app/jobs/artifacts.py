"""Flexible artifact evidence for selected-output pipelines."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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


def cleanup_completed_audio(job_dir: Path) -> dict[str, Any]:
    """Remove only derived audio after a successful, evidenced pipeline run.

    Raw provider JSON/transcripts and all user-facing exports are deliberately
    untouched.  The audit file makes the destructive part resumable and
    observable; failures are reported rather than hidden.
    """
    candidates = [job_dir / "normalized.flac", job_dir / "normalized.tmp.flac"]
    candidates.extend((job_dir / "chunks").glob("chunk-*/audio.flac"))
    candidates.extend((job_dir / "chunks").glob("chunk-*/audio.flac.tmp"))
    candidates.extend(job_dir.glob("*.partial"))
    removed: list[str] = []
    errors: list[dict[str, str]] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed.append(str(path.relative_to(job_dir)))
        except OSError as exc:
            errors.append({"path": str(path.relative_to(job_dir)), "error": str(exc)})
    report = {
        "version": "audio-cleanup-v1",
        "policy": "completed-only-derived-audio",
        "generated_at": datetime.now(UTC).isoformat(),
        "removed": removed,
        "errors": errors,
        "status": "PASS" if not errors else "REVIEW",
    }
    temporary = job_dir / "audio-cleanup.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(job_dir / "audio-cleanup.json")
    return report


def install_artifact_patch(worker_module: Any) -> None:
    worker_module._artifact_evidence = artifact_evidence
