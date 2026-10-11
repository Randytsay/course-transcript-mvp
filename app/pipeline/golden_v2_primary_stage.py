"""Isolated V2 primary stage: SHA checked FLAC → private GCS → Gemini → ledger.

This module is never invoked by existing V1 jobs, HTTP handlers or imports.
The caller must acquire an approved SQLite V2 stage claim and supply an
owner-authorized storage client and Vertex client. Unit tests use mock clients.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.gemini_primary_audio import (
    assemble_windows, run_window, PrimaryTranscriptError,
)
from app.providers.golden_v2_audio_staging import prepare_local_clips
from app.providers.golden_v2_gcs_staging import upload_approved_clips
from app.providers.golden_v2_source_lineage import bind_source_lineage
from app.providers.golden_v2_immutable import store_immutable_bytes


def _persist_immutable(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return store_immutable_bytes(path, encoded)


def execute_approved_primary_stage(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    normalized_audio: Path, job_dir: Path,
    original_media: Path, drive_observation: dict[str, Any],
    storage_client: Any, vertex_client: Any,
    bucket_name: str, object_prefix: str,
    cost_accountant: Callable[[list[dict[str, Any]]], int],
    cost_estimator: Callable[[dict[str, Any]], int],
    part_factory: Any = None,
    max_window_seconds: float = 2700.0,
    overlap_seconds: float = 10.0,
) -> dict[str, Any]:
    """One explicitly approved, bounded V2 primary stage.

    Does not start/retry jobs itself. The persistent runner must call
    claim_next exactly once and handle any REVIEW_REQUIRED/OUTCOME_UNKNOWN
    state before submitting further external API requests.
    """
    if (claim.get("stage") != "gemini_primary" or not callable(cost_accountant)
            or not callable(cost_estimator)):
        raise V2Conflict("verified Gemini claim and cost estimator/accountant required")
    ledger.assert_upload_authorized(claim, stage="gemini_primary", source_sha=claim["source_sha"])
    lineage = bind_source_lineage(
        original_media=original_media, normalized_audio=normalized_audio,
        drive_observation=drive_observation, expected_normalized_sha256=claim["source_sha"],
        output_path=job_dir / "golden-v2" / "source-lineage.json",
    )
    local = prepare_local_clips(
        source=normalized_audio,
        expected_source_sha256=claim["source_sha"],
        output_dir=job_dir / "golden-v2" / "local-clips",
        max_window_seconds=max_window_seconds,
        overlap_seconds=overlap_seconds,
    )
    estimated_cents = cost_estimator(local)
    if (type(estimated_cents) is not int or estimated_cents < 0
            or estimated_cents > claim["estimate_cents"]):
        raise V2Conflict("audio-window cost estimate exceeds approved stage reservation")
    staged = upload_approved_clips(
        ledger=ledger, claim=claim, local_manifest=local,
        local_dir=job_dir / "golden-v2" / "local-clips",
        bucket_name=bucket_name, object_prefix=object_prefix,
        storage_client=storage_client,
    )
    records: list[dict[str, Any]] = []
    known_review = False
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
    except PrimaryTranscriptError as exc:
        if exc.known_record is not None:
            records.append(exc.known_record)
            known_review = True
        else:
            # A disconnected response can have been billed; never replay.
            partial = {
                "schema": "golden-v2-ambiguous-primary-v1", "job_id": claim["job_id"],
                "source_sha256": claim["source_sha"], "completed_records": records,
                "unresolved_window_index": len(records),
            }
            digest = _persist_immutable(
                job_dir / "golden-v2" / "primary-ambiguous.json", partial,
            )
            try:
                known_cents = cost_accountant(records)
                if type(known_cents) is not int or known_cents < 0:
                    raise ValueError("unknown partial provider usage")
            except Exception:
                known_cents = 0  # Keep raw evidence; unresolved cost blocks next claim.
            ledger.mark_outcome_unknown(claim, known_cents=known_cents,
                                        evidence_sha=digest)
            raise
    try:
        actual_cents = cost_accountant(records)
    except Exception:
        ledger.mark_outcome_unknown(claim)
        raise
    if type(actual_cents) is not int or actual_cents < 0:
        ledger.mark_outcome_unknown(claim)
        raise V2Conflict("provider usage could not be reconciled to approved USD cents")
    main = (assemble_windows(staged, records, local["source_duration_seconds"])
            if not known_review else {
                "text": "\n\n".join(r.get("text", "") for r in records),
                "window_count": len(records), "structural_coverage": "INCOMPLETE",
                "acoustic_coverage": "UNVERIFIED",
                "review_items": [{"reason": "known_provider_response_requires_review",
                                  "window": len(records)-1}],
                "ready_for_chirp_comparison": False, "golden_approved": False,
            })
    report = {
        "schema": "golden-v2-primary-stage-v1",
        "job_id": claim["job_id"],
        "source_sha256": claim["source_sha"],
        "source_lineage_sha256": lineage["manifest_sha256"],
        "mode": "gemini_3_8_audio_first",
        "windows": staged,
        "window_evidence": [{
            "identity": record["identity"],
            "usage_metadata": record.get("usage_metadata", {}),
            "finish_reason": record.get("finish_reason"),
        } for record in records],
        "assembled": main,
        "estimated_cents": estimated_cents,
        "actual_cents": actual_cents,
        "ready_for_chirp": main["ready_for_chirp_comparison"],
        "golden_approved": False,
    }
    digest = _persist_immutable(job_dir / "golden-v2" / "primary-stage.json", report)
    if actual_cents > claim["estimate_cents"]:
        ledger.mark_outcome_unknown(claim, known_cents=actual_cents,
                                    evidence_sha=digest)
        raise V2Conflict("observed primary expense exceeds reservation; owner reconciliation required")
    if main["ready_for_chirp_comparison"]:
        ledger.complete(claim, evidence_sha=digest, actual_cents=actual_cents)
    else:
        ledger.hold_for_review(claim, evidence_sha=digest, actual_cents=actual_cents)
    return report
