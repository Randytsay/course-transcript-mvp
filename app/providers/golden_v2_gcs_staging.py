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
    if claim.get("stage") not in {"gemini_primary", "chirp_independent", "third_optional"}:
        raise AudioStagingError("GCS staging allowed only for approved audio stages")
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
    stage_limit = {"third_optional": 900.0, "chirp_independent": 1200.0,
                   "gemini_primary": 2700.0}[claim["stage"]]
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
                or remote_meta.get("golden_v2_stage") != claim["stage"]
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
                or meta.get("golden_v2_stage") != claim["stage"]
                or int(blob.size or 0) != path.stat().st_size
            ):
                raise AudioStagingError("uploaded GCS object did not verify")
        # This is the exact bound object authorized by the provider adapter.
        if claim["stage"] != "chirp_independent":
            ledger.register_window(claim, window)
        else:
            # Submission is bound later to the exact SHA-verified uploaded
            # object, source offsets and approved per-window reservation.
            window["clip_sha256"] = digest
    return staged_windows


def upload_approved_repair_clips(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    local_manifest: dict[str, Any], local_dir: Path,
    bucket_name: str, object_prefix: str, storage_client: Any,
) -> list[dict[str, Any]]:
    """Upload only plan-bound, owner-approved bounded Standard repair clips."""
    from json import loads

    if (claim.get("stage") != "chirp_independent"
            or local_manifest.get("schema") != "golden-v2-local-repair-windows-v1"
            or local_manifest.get("source_sha256") != claim.get("source_sha")
            or not bucket_name or "/" in bucket_name or not _prefix_ok(object_prefix)):
        raise AudioStagingError("invalid approved Standard repair upload context")
    source_sha = claim["source_sha"]
    persisted = {row["window_id"]: row for row in ledger.chirp_windows(claim["job_id"])
                 if row.get("purpose") == "REPAIR"}
    windows = local_manifest.get("windows")
    if not isinstance(windows, list) or not windows:
        raise AudioStagingError("no bounded repair clips to upload")
    root = local_dir.resolve(strict=True)
    staged = []
    files = []
    for item in windows:
        if not isinstance(item, dict):
            raise AudioStagingError("invalid local repair clip")
        window_id, name = item.get("window_id"), item.get("clip_filename")
        row = persisted.get(window_id)
        if (row is None or row["status"] != "PLANNED"
                or row["source_sha"] != source_sha
                or row["clip_sha"] != item.get("clip_sha256")
                or row["start_ms"] != item.get("start_ms")
                or row["end_ms"] != item.get("end_ms")
                or loads(row["target_intervals_json"]) != [
                    {"start_ms": t["start_ms"], "end_ms": t["end_ms"]}
                    for t in item.get("target_intervals", [])]):
            raise AudioStagingError("repair clip is not bound to an approved durable operation")
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".flac"):
            raise AudioStagingError("invalid repair clip filename")
        path = (root / name).resolve(strict=True)
        if (path.parent != root or not path.is_file()
                or file_sha256(path) != row["clip_sha"]
                or path.stat().st_size != item.get("clip_bytes")):
            raise AudioStagingError("repair clip changed after approval")
        key = row["gcs_uri"].removeprefix(f"gs://{bucket_name}/")
        if (key == row["gcs_uri"] or not key.startswith(object_prefix + "/")
                or key.endswith("/") or ".." in key.split("/")):
            raise AudioStagingError("repair GCS input URI escapes the approved private prefix")
        staged.append({"window_id": window_id, "gcs_uri": row["gcs_uri"],
                       "clip_sha256": row["clip_sha"], "start_ms": row["start_ms"],
                       "end_ms": row["end_ms"], "target_intervals": loads(row["target_intervals_json"])})
        files.append((path, key, row["clip_sha"]))

    # Verify the live paid-stage claim only after every local file and operation
    # has passed validation, before the first external storage call.
    ledger.assert_upload_authorized(claim, stage="chirp_independent", source_sha=source_sha)
    bucket = storage_client.bucket(bucket_name)
    for window, (path, key, digest) in zip(staged, files):
        blob = bucket.blob(key)
        if blob.exists(client=storage_client):
            blob.reload(client=storage_client)
            meta = blob.metadata or {}
            if (meta.get("golden_v2_clip_sha256") != digest
                    or meta.get("golden_v2_source_sha256") != source_sha
                    or meta.get("golden_v2_stage") != "chirp_independent_repair"
                    or int(blob.size or 0) != path.stat().st_size):
                raise AudioStagingError("existing repair object failed immutable identity check")
        else:
            blob.metadata = {
                "golden_v2_clip_sha256": digest,
                "golden_v2_source_sha256": source_sha,
                "golden_v2_stage": "chirp_independent_repair",
            }
            blob.upload_from_filename(str(path), content_type="audio/flac", if_generation_match=0)
            blob.reload(client=storage_client)
            meta = blob.metadata or {}
            if (meta.get("golden_v2_clip_sha256") != digest
                    or meta.get("golden_v2_source_sha256") != source_sha
                    or meta.get("golden_v2_stage") != "chirp_independent_repair"
                    or int(blob.size or 0) != path.stat().st_size):
                raise AudioStagingError("uploaded repair object failed SHA verification")
    return staged
