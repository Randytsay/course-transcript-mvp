"""One-use, private, source-bound upload for a single 240s acceptance clip.

Cloud code runs ONLY when a separate trusted operator injects an authorized
storage client and two independently-backed verifiers. No credentials are
loaded here. Ambiguous upload blocks retries until manual reconciliation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from app.pipeline.golden_v2_smoke_ledger import (
    SingleClipSmokeLedger, SmokeConflict, _receipt_once, _sha,
)
from app.providers.golden_v2_smoke_providers import require_exact_smoke_uri


def _authorized_scope(
    trial: SingleClipSmokeLedger,
    *, check_owner: Callable[[dict[str, Any]], bool],
    check_bucket: Callable[[dict[str, Any], str], bool],
    bucket: str,
) -> dict[str, Any]:
    plan = trial._scope()
    if (not callable(check_owner) or not callable(check_bucket)
            or check_owner(plan) is not True
            or check_bucket(plan, bucket) is not True):
        raise SmokeConflict("trusted staging owner/bucket approval missing")
    return plan


def stage_clip_once(
    *, trial: SingleClipSmokeLedger, storage_client: Any,
    bucket: str, check_owner: Callable[[dict[str, Any]], bool],
    check_bucket: Callable[[dict[str, Any], str], bool],
) -> dict[str, Any]:
    """Strict if_generation_match=0 GCS upload; never transparently retries."""
    plan = _authorized_scope(
        trial, check_owner=check_owner, check_bucket=check_bucket,
        bucket=bucket)
    if trial.status()["provider_calls_started"]:
        raise SmokeConflict("cannot upload after any paid model request")
    clip_sha = plan["clip_sha256"]
    key = f"golden-v2-smoke/{clip_sha}/clip.flac"
    uri = "gs://" + bucket + "/" + key
    require_exact_smoke_uri(uri, bucket=bucket, clip_sha256=clip_sha)
    if trial.excerpt_audio.stat().st_size != 8241422:
        raise SmokeConflict("four-minute sample byte size changed")
    if _sha(trial.excerpt_audio) != clip_sha:
        raise SmokeConflict("four-minute sample checksum changed")
    receipt_base = trial.plan_path.parent
    intent = receipt_base / "gcs-upload-intent.json"
    receipt = receipt_base / "gcs-upload-complete.json"
    _receipt_once(intent, {
        "schema": "golden-v2-smoke-upload-intent-v1",
        "uri": uri, "clip_sha256": clip_sha,
        "parent_drive_id": plan["source_drive_file_id"],
        "parent_drive_version": str(plan["source_drive_version"]),
        "mode": "CREATE_ONLY_NO_AUTORETRY",
    })
    # Only after O_EXCL/fsynced intent may a GCS mutation occur.
    blob = storage_client.bucket(bucket).blob(key)
    blob.metadata = {
        "golden-v2-clip-sha256": clip_sha,
        "golden-v2-parent-sha256": plan["source_original_sha256"],
    }
    blob.upload_from_filename(
        str(trial.excerpt_audio), content_type="audio/flac",
        if_generation_match=0, checksum="auto", timeout=90, retry=None,
    )
    generation = getattr(blob, "generation", None)
    if generation is None:
        raise SmokeConflict("GCS upload generation unavailable")
    evidence = {
        "schema": "golden-v2-smoke-gcs-object-v1",
        "uri": uri, "clip_sha256": clip_sha,
        "bytes": trial.excerpt_audio.stat().st_size,
        "generation": str(generation), "gcs_mutation_started": True,
        "provider_calls_started": False,
    }
    _receipt_once(receipt, evidence)
    return evidence


def verify_staged_clip(
    *, trial: SingleClipSmokeLedger, storage_client: Any,
    bucket: str,
) -> dict[str, Any]:
    """GET actual bytes of the 4-minute clip; no receipt alone grants trust."""
    plan = trial._scope()
    receipt = trial.plan_path.parent / "gcs-upload-complete.json"
    if receipt.is_symlink() or not receipt.is_file():
        raise SmokeConflict("no verified private GCS upload receipt")
    data = json.loads(receipt.read_text(encoding="utf-8"))
    if data.get("schema") != "golden-v2-smoke-gcs-object-v1":
        raise SmokeConflict("unexpected GCS source receipt")
    uri = data.get("uri")
    clip_sha = plan["clip_sha256"]
    key = require_exact_smoke_uri(uri, bucket=bucket, clip_sha256=clip_sha)
    if (data.get("clip_sha256") != clip_sha
            or data.get("bytes") != 8241422
            or not isinstance(data.get("generation"), str)
            or not data["generation"].isdigit()):
        raise SmokeConflict("staged object receipt does not match authorized clip")
    blob = storage_client.bucket(bucket).blob(key)
    payload = blob.download_as_bytes(
        if_generation_match=int(data["generation"]),
        timeout=45, retry=None,
    )
    if (not isinstance(payload, bytes) or len(payload) != data["bytes"]
            or hashlib.sha256(payload).hexdigest() != clip_sha):
        raise SmokeConflict("GCS bytes differ from approved short source")
    return {
        "uri": uri, "clip_sha256": clip_sha,
        "generation": data["generation"],
        "gcs_downloaded_sha256": hashlib.sha256(payload).hexdigest(),
        "verified_without_mutation": True,
    }