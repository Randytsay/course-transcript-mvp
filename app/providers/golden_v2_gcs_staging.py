"""Opt-in, approval-bound upload of V2 FLAC clips to an existing private GCS bucket.

This module DOES issue storage API calls if invoked. It is NOT executed by
imports, tests, or V1. The caller must supply the already-approved private
bucket client and durable V2 claim. Never create buckets or change bucket IAM.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.providers.gemini_primary_audio import validate_staged_windows
from app.providers.golden_v2_audio_staging import file_sha256, AudioStagingError


def _prefix_ok(value: str) -> bool:
    return bool(value) and all(
        part not in {"", ".", ".."} and part.isascii()
        and all(ch.isalnum() or ch in "_-." for ch in part)
        for part in value.strip("/").split("/")
    ) and value == value.strip("/")


def upload_approved_clips(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    local_manifest: dict[str, Any], local_dir: Path,
    bucket_name: str, object_prefix: str,
    storage_client: Any,
) -> list[dict[str, Any]]:
    """Upload content-addressed blobs without destructive overwrite.

    The normalized-FLAC SHA is the claim's source ID. The V2 job's original
    Drive provenance is tracked separately by the ingestion/preflight layer.
    All external calls are behind assert_upload_authorized().
    """
    if claim.get("stage") not in {"gemini_primary", "third_optional"}:
        raise AudioStagingError("GCS staging allowed only for Gemini audio stages")
    if not bucket_name or "/" in bucket_name or not _prefix_ok(object_prefix):
        raise AudioStagingError("unsafe bucket or destination prefix")
    if local_manifest.get("schema") != "golden-v2-local-audio-windows-v1":
        raise AudioStagingError("unrecognized local window manifest")
    source_sha = str(local_manifest.get("source_sha256", ""))
    if local_manifest.get("source_kind") != "normalized_flac_sha256" or source_sha != claim.get("source_sha"):
        raise AudioStagingError("local source differs from approved stage source")
    windows = local_manifest.get("windows")
    if not isinstance(windows, list) or not windows:
        raise AudioStagingError("no staged clips")
    stage_limit = 900.0 if claim["stage"] == "third_optional" else 2700.0
    # Fully validate all windows and local bytes before *any* network call.
    staged_windows = []
    files = []
    root = local_dir.resolve(strict=True)
    for i, row in enumerate(windows):
        if not isinstance(row, dict) or int(row["index"]) != i:
            raise AudioStagingError("out-of-order local clips")
        name = str(row.get("clip_filename", ""))
        if not name or Path(name).name != name or not name.endswith(".flac"):
            raise AudioStagingError("invalid clip filename")
        path = (root / name).resolve(strict=True)
        if path.parent != root or not path.is_file():
            raise AudioStagingError("clip escapes staged directory")
        digest = str(row.get("clip_sha256", ""))
        if len(digest) != 64 or file_sha256(path) != digest or path.stat().st_size != row.get("clip_bytes"):
            raise AudioStagingError("local staged clip was changed")
        key = f"{object_prefix}/{claim['job_id']}/{source_sha}/{i:04d}-{digest}.flac"
        candidate = {
            "index": i,
            "start_seconds": row["start_seconds"],
            "end_seconds": row["end_seconds"],
            "gcs_uri": f"gs://{bucket_name}/{key}",
            "mime_type": "audio/flac",
        }
        staged_windows.append(candidate)
        files.append((path, key, digest))
    validate_staged_windows(staged_windows, float(local_manifest["source_duration_seconds"]), max_seconds=stage_limit)
    ledger.assert_upload_authorized(claim, stage=claim["stage"], source_sha=source_sha)
    bucket = storage_client.bucket(bucket_name)
    for window, (path, key, digest) in zip(staged_windows, files):
        blob = bucket.blob(key)
        if blob.exists(client=storage_client):
            # Never rewrite a prior remote object. Safe reuse requires
            # cloud-returned size AND trusted metadata matching the local clip.
            blob.reload(client=storage_client)
            remote_meta = blob.metadata or {}
            if (
                str(remote_meta.get("golden_v2_clip_sha256", "")) != digest
                or str(remote_meta.get("golden_v2_source_sha256", "")) != source_sha
                or int(blob.size or 0) != path.stat().st_size
            ):
                raise AudioStagingError("existing remote clip failed identity verification")
        else:
            blob.metadata = {
                "golden_v2_clip_sha256": digest,
                "golden_v2_source_sha256": source_sha,
                "golden_v2_stage": claim["stage"],
            }
            # Cloud Storage generation precondition protects concurrent writers.
            blob.upload_from_filename(str(path), content_type="audio/flac", if_generation_match=0)
            blob.reload(client=storage_client)
            meta = blob.metadata or {}
            if (
                str(meta.get("golden_v2_clip_sha256", "")) != digest
                or str(meta.get("golden_v2_source_sha256", "")) != source_sha
                or int(blob.size or 0) != path.stat().st_size
            ):
                raise AudioStagingError("uploaded GCS object did not verify")
        # This is the exact bound object authorized by the provider adapter.
        ledger.register_window(claim, window)
    return staged_windows
