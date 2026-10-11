"""Isolated V2 primary stage: SHA checked FLAC → private GCS → Gemini → ledger.

This module is never invoked by existing V1 jobs, HTTP handlers or imports.
The caller must acquire an approved SQLite V2 stage claim and supply an
owner-authorized storage client and Vertex client. Unit tests use mock clients.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.gemini_primary_audio import (
    assemble_windows, run_window, _atomic_json, PrimaryTranscriptError,
)
from app.providers.golden_v2_audio_staging import prepare_local_clips
from app.providers.golden_v2_gcs_staging import upload_approved_clips


def _persist_immutable(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        existing = json.dumps(previous, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if existing != encoded:
            raise V2Conflict("existing primary stage result disagrees; operator review required")
    else:
        _atomic_json(path, payload)
    return digest


def execute_approved_primary_stage(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    normalized_audio: Path, job_dir: Path,
    storage_client: Any, vertex_client: Any,
    bucket_name: str, object_prefix: str,
    cost_accountant: Callable[[list[dict[str, Any]]], int],
    part_factory: Any = None,
    max_window_seconds: float = 2700.0,
    overlap_seconds: float = 10.0,
) -> dict[str, Any]:
    """One explicitly approved, bounded V2 primary stage.

    Does not start/retry jobs itself. The persistent runner must call
    claim_next exactly once and handle any REVIEW_REQUIRED/OUTCOME_UNKNOWN
    state before submitting further external API requests.
    """
    if claim.get("stage") != "gemini_primary" or not callable(cost_accountant):
        raise V2Conflict("verified Gemini stage claim and actual-cost accountant required")
    ledger.assert_upload_authorized(claim, stage="gemini_primary", source_sha=claim["source_sha"])
    local = prepare_local_clips(
        source=normalized_audio,
        expected_source_sha256=claim["source_sha"],
        output_dir=job_dir / "golden-v2" / "local-clips",
        max_window_seconds=max_window_seconds,
        overlap_seconds=overlap_seconds,
    )
    staged = upload_approved_clips(
        ledger=ledger, claim=claim, local_manifest=local,
        local_dir=job_dir / "golden-v2" / "local-clips",
        bucket_name=bucket_name, object_prefix=object_prefix,
        storage_client=storage_client,
    )
    records: list[dict[str, Any]] = []
    try:
        for window in staged:
            records.append(run_window(
                client=vertex_client,
                staged_window=window,
                source_sha256=claim["source_sha"],
                job_dir=job_dir,
                approval_token=claim["claim"],
                part_factory=part_factory,
                approval_validator=ledger.provider_approval_validator(claim),
            ))
    except PrimaryTranscriptError:
        # Even a network error may have consumed tokens; an operator must
        # reconcile the corresponding raw provider evidence and actual costs.
        ledger.mark_outcome_unknown(claim)
        raise
    actual_cents = cost_accountant(records)
    if type(actual_cents) is not int or actual_cents < 0:
        ledger.mark_outcome_unknown(claim)
        raise V2Conflict("provider usage could not be reconciled to approved USD cents")
    main = assemble_windows(staged, records, local["source_duration_seconds"])
    report = {
        "schema": "golden-v2-primary-stage-v1",
        "job_id": claim["job_id"],
        "source_sha256": claim["source_sha"],
        "mode": "gemini_3_8_audio_first",
        "windows": staged,
        "window_evidence": [{
            "identity": record["identity"],
            "usage_metadata": record.get("usage_metadata", {}),
            "finish_reason": record.get("finish_reason"),
        } for record in records],
        "assembled": main,
        "actual_cents": actual_cents,
        "ready_for_chirp": main["ready_for_chirp_comparison"],
        "golden_approved": False,
    }
    digest = _persist_immutable(job_dir / "golden-v2" / "primary-stage.json", report)
    if main["ready_for_chirp_comparison"]:
        ledger.complete(claim, evidence_sha=digest, actual_cents=actual_cents)
    else:
        ledger.hold_for_review(claim, evidence_sha=digest, actual_cents=actual_cents)
    return report
