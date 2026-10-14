"""Opt-in Gemini 3.5 transcription stage with durable, approval-bound receipts.

Called only after the ledger's comparison stage; never schedules paid work on
import. All audio source evidence is checked before any private GCS upload.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_primary_stage import _persist_immutable
from app.providers.gemini_primary_audio import PrimaryTranscriptError, validate_staged_windows
from app.providers.gemini_third_transcribe import run_third_window, MAX_WINDOW_SECONDS
from app.providers.golden_v2_audio_staging import prepare_local_clips
from app.providers.golden_v2_gcs_staging import upload_approved_clips
from app.providers.golden_v2_source_lineage import bind_source_lineage
from app.pipeline.golden_v2_window_budget import check_paid_window_budget


def execute_approved_third_stage(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    normalized_audio: Path, original_media: Path,
    drive_observation: dict[str, Any], job_dir: Path,
    storage_client: Any, vertex_client: Any,
    bucket_name: str, object_prefix: str,
    cost_estimator: Callable[[dict[str, Any]], int],
    cost_accountant: Callable[[list[dict[str, Any]]], int],
    custom_vocabulary: list[str] | None = None,
    word_timestamp: bool = False, diarization: bool = False,
    part_factory: Any = None, config_factory: Any = None,
    max_window_seconds: float = MAX_WINDOW_SECONDS,
    overlap_seconds: float = 0.0,
) -> dict[str, Any]:
    """Finish or hold an explicitly claimed third-model stage.

    A prior SMART_2PLUS decision and FLASH_CHIRP skip are enforced by the
    ledger's claim_next: this entry point never overrides those decisions.
    """
    if (claim.get("stage") != "third_optional" or not callable(cost_estimator)
            or not callable(cost_accountant)):
        raise V2Conflict("approved third-model claim and verified cost functions required")
    if not 0 < max_window_seconds <= MAX_WINDOW_SECONDS:
        raise V2Conflict("Vertex third-model window exceeds conservative 15 minute limit")
    ledger.assert_upload_authorized(claim, stage="third_optional",
                                    source_sha=claim["source_sha"])
    lineage = bind_source_lineage(
        original_media=original_media, normalized_audio=normalized_audio,
        drive_observation=drive_observation,
        expected_normalized_sha256=claim["source_sha"],
        output_path=job_dir / "golden-v2" / "source-lineage.json",
    )
    local = prepare_local_clips(
        source=normalized_audio, expected_source_sha256=claim["source_sha"],
        output_dir=job_dir / "golden-v2" / "third-local-clips",
        max_window_seconds=max_window_seconds,
        overlap_seconds=overlap_seconds,
    )
    estimated_cents = cost_estimator(local)
    if (type(estimated_cents) is not int or estimated_cents < 0
            or estimated_cents > claim["estimate_cents"]):
        raise V2Conflict("third-model estimate exceeds preapproved reservation")
    staged = upload_approved_clips(
        ledger=ledger, claim=claim, local_manifest=local,
        local_dir=job_dir / "golden-v2" / "third-local-clips",
        storage_client=storage_client, bucket_name=bucket_name,
        object_prefix=object_prefix,
    )
    validate_staged_windows(staged, local["source_duration_seconds"],
                            max_seconds=MAX_WINDOW_SECONDS)
    records: list[dict[str, Any]] = []
    known_review = False
    try:
        for window in staged:
            records.append(run_third_window(
                client=vertex_client, staged_window=window,
                source_sha256=claim["source_sha"], job_dir=job_dir,
                approval_token=claim["claim"], enabled=True,
                custom_vocabulary=custom_vocabulary,
                word_timestamp=word_timestamp, diarization=diarization,
                part_factory=part_factory, config_factory=config_factory,
                approval_validator=ledger.provider_approval_validator(claim),
            ))
            # Never send the next paid third-model request when previously
            # returned usage is already over budget or cannot be priced.
            check_paid_window_budget(
                ledger=ledger, claim=claim, job_dir=job_dir,
                completed_records=records, accountant=cost_accountant,
            )
    except PrimaryTranscriptError as exc:
        if exc.known_record is not None:
            records.append(exc.known_record)
            known_review = True
        else:
            digest = _persist_immutable(
                job_dir / "golden-v2" / "third-ambiguous.json", {
                    "schema": "golden-v2-ambiguous-third-v1",
                    "job_id": claim["job_id"], "source_sha256": claim["source_sha"],
                    "completed_records": records, "unresolved_window_index": len(records),
                },
            )
            try:
                known_cents = cost_accountant(records)
                if type(known_cents) is not int or known_cents < 0:
                    raise ValueError("unaccounted partial usage")
            except Exception:
                known_cents = 0
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
        raise V2Conflict("third model actual usage could not be reconciled")
    overlaps = any(
        staged[index]["start_seconds"] < staged[index - 1]["end_seconds"]
        for index in range(1, len(staged))
    )
    ready = (not known_review and not overlaps and len(records) == len(staged)
             and all(record.get("status") == "COMPLETE" for record in records))
    report = {
        "schema": "golden-v2-third-stage-v1", "job_id": claim["job_id"],
        "source_sha256": claim["source_sha"],
        "source_lineage_sha256": lineage["manifest_sha256"],
        "model": "gemini-3.5-transcribe", "source_windows": staged,
        "window_evidence": records,
        "text": "\n\n".join(str(record.get("text", "")) for record in records),
        "estimated_cents": estimated_cents, "actual_cents": actual_cents,
        "needs_review": not ready, "ready_for_adjudication": ready,
        "timing_authority": "CHIRP_3_ONLY", "golden_approved": False,
    }
    digest = _persist_immutable(job_dir / "golden-v2" / "third-stage.json", report)
    if actual_cents > claim["estimate_cents"]:
        ledger.mark_outcome_unknown(claim, known_cents=actual_cents,
                                    evidence_sha=digest)
        raise V2Conflict("third-model cost overrun requires reconciliation")
    if ready:
        ledger.complete(claim, evidence_sha=digest, actual_cents=actual_cents)
    else:
        ledger.hold_for_review(claim, evidence_sha=digest, actual_cents=actual_cents)
    return report
