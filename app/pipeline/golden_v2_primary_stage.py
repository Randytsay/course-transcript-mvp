"""Isolated V2 primary stage: SHA checked FLAC → private GCS → Gemini → ledger.

This module is never invoked by existing V1 jobs, HTTP handlers or imports.
The caller must acquire an approved SQLite V2 stage claim and supply an
owner-authorized storage client and Vertex client. Unit tests use mock clients.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.gemini_primary_audio import (
    PROMPT_VERSION, assemble_windows, assemble_reviewed_overlaps,
    run_window, PrimaryTranscriptError, validate_staged_windows,
    _paid_window_slot_digest,
)
from app.providers.golden_v2_audio_staging import file_sha256, prepare_local_clips
from app.providers.golden_v2_gcs_staging import upload_approved_clips
from app.providers.golden_v2_source_lineage import bind_source_lineage
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.pipeline.golden_v2_window_budget import check_paid_window_budget


def _persist_immutable(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return store_immutable_bytes(path, encoded)


def _primary_audit_path(job_dir: Path, identity: dict[str, Any]) -> Path:
    digest = _paid_window_slot_digest(identity)
    return job_dir / "gemini-primary" / f"{digest}.json"


def verify_primary_overlap_artifacts(
    *, job_dir: Path, job_id: str, source_sha: str, original_sha: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read-only, fail-closed verification shared by inspection and approval.

    Exact SHA bindings cover source lineage, staged windows, each immutable
    provider audit and the initial overlap-only assembly. Historic reports
    lacking raw audit checksums cannot be approved without reconciliation.
    """
    def valid_sha(value: Any) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(ch in "0123456789abcdef" for ch in value))

    try:
        path = job_dir / "golden-v2" / "primary-stage.json"
        if not valid_sha(original_sha) or not path.is_file() or file_sha256(path) != original_sha:
            raise V2Conflict("original immutable primary report missing or changed")
        report = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(report, dict)
                or report.get("schema") != "golden-v2-primary-stage-v1"
                or report.get("job_id") != job_id
                or report.get("source_sha256") != source_sha
                or report.get("mode") != "gemini_3_8_audio_first"
                or report.get("ready_for_chirp") is not False
                or report.get("golden_approved") is not False):
            raise V2Conflict("invalid primary overlap review report")

        lineage_sha = report.get("source_lineage_sha256")
        lineage_path = job_dir / "golden-v2" / "source-lineage.json"
        if (not valid_sha(lineage_sha) or not lineage_path.is_file()
                or file_sha256(lineage_path) != lineage_sha):
            raise V2Conflict("immutable source lineage checksum mismatch")
        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
        if (not isinstance(lineage, dict)
                or lineage.get("schema") != "golden-v2-source-lineage-v1"
                or lineage.get("normalized_sha256") != source_sha
                or not valid_sha(lineage.get("original_sha256"))):
            raise V2Conflict("invalid audio source lineage")

        windows, evidence = report.get("windows"), report.get("window_evidence")
        if (not isinstance(windows, list) or not isinstance(evidence, list)
                or len(windows) < 2 or len(windows) != len(evidence)
                or not all(isinstance(w, dict) for w in windows)
                or not all(isinstance(e, dict) for e in evidence)):
            raise V2Conflict("invalid primary window evidence structure")
        for index, window in enumerate(windows):
            if (type(window.get("index")) is not int or window["index"] != index
                    or any(type(window.get(key)) not in (int, float)
                           or not math.isfinite(window[key])
                           for key in ("start_seconds", "end_seconds"))):
                raise V2Conflict("invalid or non-finite staged window")
        duration = float(windows[-1]["end_seconds"])
        if not math.isfinite(duration):
            raise V2Conflict("invalid primary window duration")
        validate_staged_windows(windows, duration)
        records: list[dict[str, Any]] = []
        for window, item in zip(windows, evidence):
            identity = item.get("identity")
            expected_window = {key: window[key] for key in (
                "index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")}
            if (not isinstance(identity, dict)
                    or identity.get("model") != "gemini-3.8-flash"
                    or identity.get("prompt_version") != PROMPT_VERSION
                    or identity.get("source_sha256") != source_sha
                    or identity.get("window") != expected_window
                    or not valid_sha(identity.get("context_sha256"))
                    or not valid_sha(item.get("raw_audit_sha256"))):
                raise V2Conflict("Gemini model, prompt or staged window identity mismatch")
            audit_path = _primary_audit_path(job_dir, identity)
            if (not audit_path.is_file()
                    or file_sha256(audit_path) != item["raw_audit_sha256"]):
                raise V2Conflict("immutable Gemini raw audit checksum mismatch")
            raw = json.loads(audit_path.read_text(encoding="utf-8"))
            if (not isinstance(raw, dict) or raw.get("identity") != identity
                    or raw.get("status") != "COMPLETE"
                    or not isinstance(raw.get("text"), str)
                    or not raw["text"].strip()
                    or raw.get("finish_reason") not in {"STOP", "FINISH_REASON_STOP"}
                    or raw.get("human_review_required") is not False
                    or raw.get("finish_reason") != item.get("finish_reason")
                    or raw.get("usage_metadata") != item.get("usage_metadata")):
                raise V2Conflict("incomplete or inconsistent Gemini raw window evidence")
            records.append(raw)

        baseline = assemble_windows(windows, records, duration)
        if (not baseline["review_items"]
                or report.get("assembled") != baseline):
            raise V2Conflict("stored primary assembly differs from immutable overlap-only evidence")
        return report, records
    except (OSError, UnicodeError, ValueError, TypeError, KeyError,
            OverflowError, PrimaryTranscriptError) as exc:
        raise V2Conflict("malformed or inconsistent primary overlap evidence") from exc


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
            # The next paid window cannot start until actual usage from all
            # completed windows is recorded and still inside the stage cap.
            check_paid_window_budget(
                ledger=ledger, claim=claim, job_dir=job_dir,
                completed_records=records, accountant=cost_accountant,
            )
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
            "raw_audit_sha256": file_sha256(_primary_audit_path(job_dir, record["identity"])),
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


def finalize_reviewed_primary_overlaps(
    *, ledger: GoldenV2Ledger, job_id: str, revision: int,
    previous_evidence_sha: str, job_dir: Path, normalized_audio: Path,
    seam_decisions: list[dict[str, Any]], reviewer: str,
    review_reference: str,
) -> dict[str, Any]:
    """Reviewer-controlled, provider-free re-entry for a paid primary stage.

    Call only from an authenticated owner/operator context AFTER listening to
    each precise overlap. No external provider or Drive mutation occurs here.
    Raw Gemini response files and the original review artifact remain intact.
    """
    status = ledger.status(job_id)
    first = status["stages"][0]
    if (status["revision"] != revision or status["status"] != "NEEDS_REVIEW"
            or first["name"] != "gemini_primary"
            or first["state"] != "REVIEW_REQUIRED"
            or first["evidence_sha"] != previous_evidence_sha
            or not reviewer.strip() or not review_reference.strip()):
        raise V2Conflict("no authorized primary overlap review pending")
    if (not normalized_audio.is_file()
            or file_sha256(normalized_audio) != status["source_sha"]):
        raise V2Conflict("audio used for overlap review is not the approved source")
    report, records = verify_primary_overlap_artifacts(
        job_dir=job_dir, job_id=job_id,
        source_sha=status["source_sha"], original_sha=previous_evidence_sha,
    )
    source_path = job_dir / "golden-v2" / "primary-stage.json"
    windows = report["windows"]
    evidence = report["window_evidence"]

    assembled = assemble_reviewed_overlaps(
        windows, records, float(windows[-1]["end_seconds"]),
        seam_decisions=seam_decisions,
    )
    reviewed = copy.deepcopy(report)
    reviewed["assembled"] = assembled
    reviewed["ready_for_chirp"] = True
    reviewed["audio_review"] = {
        "original_primary_evidence_sha256": previous_evidence_sha,
        "raw_window_audit_sha256": [item["raw_audit_sha256"] for item in evidence],
        "reviewer": reviewer, "review_reference": review_reference,
        "review_scope": "OVERLAP_SEAMS_ONLY", "whole_file_qa": "UNVERIFIED",
    }
    encoded = json.dumps(reviewed, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path = job_dir / "golden-v2" / f"primary-stage-reviewed-{digest}.json"
    _persist_immutable(path, reviewed)

    def verify(*, job_id: str, source_sha: str, old_sha: str, reviewed_sha: str) -> bool:
        return (job_id == report["job_id"] and source_sha == report["source_sha256"]
                and old_sha == previous_evidence_sha and reviewed_sha == digest
                and file_sha256(source_path) == old_sha and file_sha256(path) == digest)

    ledger.complete_reviewed_primary(
        job_id, revision=revision, reviewer=reviewer,
        review_reference=review_reference,
        previous_evidence_sha=previous_evidence_sha,
        reviewed_evidence_sha=digest, evidence_verifier=verify,
    )
    return {"job_id": job_id, "reviewed_sha256": digest,
            "ready_for_chirp": True, "next_stage": "chirp_independent",
            "paid_provider_calls_started": False, "drive_mutated": False}
