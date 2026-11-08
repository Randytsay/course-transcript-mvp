"""Read-only live Drive revision check before any Golden V2 paid claim.

This check is deliberately separate from the SQLite claim. A trusted paid
executor must call it immediately before a revision-bound claim and must still
perform its own provider, pricing and approval checks. The check never grants
permission to submit a billable request by itself.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from app.jobs.drive_api import read_drive_media_metadata
from app.pipeline.golden_v2_paid_readiness import inspect_paid_readiness


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_live_drive_revision(
    *, database: Path, artifact_root: Path, job_id: str,
    read_metadata: Callable[[str], dict[str, Any]] = read_drive_media_metadata,
    max_approved_cents: int = 1000,
) -> dict[str, Any]:
    """Compare fresh Drive metadata with the approved immutable local source.

    The injected reader is for offline tests; production callers use the
    authenticated Drive API. No SQLite write, provider POST or Drive upload
    occurs here. A changed path/object/version/MD5 blocks the job.
    """
    readiness = inspect_paid_readiness(
        database=database, artifact_root=artifact_root, job_id=job_id,
        max_approved_cents=max_approved_cents,
    )
    blockers = set(readiness["blockers"])
    checked = False
    if not blockers:
        root = Path(artifact_root).expanduser().resolve()
        job_dir = root / "jobs" / job_id
        try:
            preflight = json.loads((job_dir / "source-preflight.json").read_text())
            observation = preflight["drive_observation"]
            source_path = preflight["drive_source_path"]
            expected_md5 = preflight["original_md5_checksum"]
            original = job_dir / "source.original"
            if (not isinstance(source_path, str) or not source_path.strip()
                    or not isinstance(expected_md5, str)
                    or _md5_file(original) != expected_md5):
                blockers.add("SOURCE_DRIVE_IDENTITY_INCOMPLETE")
            else:
                current = read_metadata(source_path)
                checked = True
                if (not isinstance(current, dict)
                        or current.get("id") != observation["file_id"]
                        or str(current.get("version")) != str(observation["version"])
                        or current.get("size_bytes") != observation["size_bytes"]
                        or current.get("md5_checksum") != expected_md5):
                    blockers.add("LIVE_DRIVE_SOURCE_CHANGED")
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            blockers.add("LIVE_DRIVE_REVISION_UNAVAILABLE")
    return {
        "schema": "golden-v2-live-drive-revision-v1",
        "job_id": job_id,
        "source_sha256": readiness["source_sha256"],
        "next_stage": readiness["next_stage"],
        "blockers": sorted(blockers),
        "live_drive_revision_verified": checked and not blockers,
        "provider_calls_allowed": False,
        "ledger_modified": False,
        "drive_mutation_started": False,
    }
