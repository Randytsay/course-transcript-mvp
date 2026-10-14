"""Fail-closed, immutable *planning only* for a derived real-audio paid smoke.

This is not a V2 source preflight registration or paid approval endpoint.
The short clip is derived from a full Drive source, and MUST NOT impersonate
the Drive file's ID/revision. A future smoke runner must independently verify
the live source, exact clip lineage, actual cost estimate, capabilities and
owner approval before any provider or GCS request.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or symlinked smoke artifact")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _data(path: Path, schema: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("symlinked smoke manifest")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != schema:
        raise ValueError("invalid smoke manifest schema")
    return data


def build_paid_smoke_plan(
    *, offline_manifest: Path, original_preflight: Path,
    original_media: Path, excerpt_audio: Path,
    max_total_cents: int = 1000,
) -> dict[str, Any]:
    """Return a source-bound, no-side-effect proposal in USD cents."""
    if type(max_total_cents) is not int or not 1 <= max_total_cents <= 1000:
        raise ValueError("trial ceiling exceeds user-authorized USD 10")
    offline = _data(offline_manifest, "golden-v2-real-audio-offline-smoke-v1")
    pre = _data(original_preflight, "golden-v2-source-preflight-v1")
    src, clip = offline["source"], offline["excerpt"]
    obs = pre["drive_observation"]
    original_sha = _sha256(original_media)
    clip_sha = _sha256(excerpt_audio)
    if (src.get("sha256") != original_sha
            or src.get("drive_file_id") != obs.get("file_id")
            or str(src.get("drive_version")) != str(obs.get("version"))
            or src.get("drive_md5") != pre.get("original_md5_checksum")
            or pre.get("original_sha256") != original_sha
            or clip.get("sha256") != clip_sha
            or clip.get("derived_from_source_sha256") != original_sha
            or clip.get("is_independent_drive_source") is not False
            or clip.get("start_seconds") != 600
            or clip.get("end_seconds") != 840
            or clip.get("duration_seconds") != 240.0
            or clip.get("size_bytes") != Path(excerpt_audio).stat().st_size
            or offline["preflight"]["job_id"] != (
                "v2p_" + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "golden-v2-source-preflight:" +
                    _sha256(original_preflight),
                ).hex
            )
            or offline["preflight"]["source_sha256"] != pre.get("source_sha256")
            or offline["preflight"]["status"] != "AWAITING_APPROVAL"):
        raise ValueError("source-derived clip provenance mismatch")
    # Upper reservations, not price quotes: no executable budget is created.
    # Unallocated buffer cannot automatically be spent by a provider.
    stages = {
        "gemini_3_8_primary_cap_cents": 200,
        "chirp_3_cap_cents": 200,
        "gemini_3_5_transcribe_cap_cents": 200,
    }
    if max_total_cents < sum(stages.values()):
        raise ValueError("trial cap too low for proposed multi-provider stages")
    return {
        "schema": "golden-v2-single-clip-paid-smoke-plan-v1",
        "source_drive_file_id": obs["file_id"],
        "source_drive_version": obs["version"],
        "source_original_sha256": original_sha,
        "parent_v2_job_id": offline["preflight"]["job_id"],
        "clip_sha256": clip_sha,
        "clip_start_seconds": 600,
        "clip_end_seconds": 840,
        "clip_duration_seconds": 240,
        "clip_is_independent_drive_source": False,
        "models_in_order": [
            "gemini-3.8-flash", "chirp_3", "gemini-3.5-transcribe",
        ],
        "cap_usd_cents": max_total_cents,
        "stage_reservation_caps_usd_cents": stages,
        "unallocated_contingency_usd_cents": max_total_cents - sum(stages.values()),
        "user_authorization_scope": "ONE_DERIVED_4MIN_CLIP_MAX_USD_10",
        "provider_pricing_verified": False,
        "cloud_identity_verified": False,
        "live_drive_revision_verified_at_execution": False,
        "approved_ledger_claim_exists": False,
        "paid_execution_enabled": False,
        "provider_calls_started": False,
        "gcs_mutation_started": False,
        "drive_mutation_started": False,
        "next_action": "TRUSTED_OPERATOR_CLOUD_AND_PRICING_CHECKS",
    }


def persist_plan_once(path: Path, plan: dict[str, Any]) -> None:
    """Never overwrite a previous authorization scope or broaden a test."""
    payload = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2)
               + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("unsafe plan path")
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("previous immutable smoke plan differs")
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)