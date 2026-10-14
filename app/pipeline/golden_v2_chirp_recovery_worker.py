"""Bounded V2 Chirp recovery pass, using only persisted operation identities.

The worker never submits a Speech request, authorizes costs, or fabricates
audio/time evidence. A restarted process may recover saved SUBMITTED LROs.
SUBMITTING remains an explicit manual reconciliation blocker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_chirp_stage import (
    finish_approved_chirp, recover_chirp_window,
    reconcile_reviewed_chirp_window,
)
from app.providers.golden_v2_audio_staging import file_sha256


def run_chirp_recovery_pass(
    *, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
    gateway: Any, cost_accountant: Callable[[dict[str, Any]], int],
    max_operations: int = 8,
    acoustic_inspector: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Process at most max_operations saved LROs; caller schedules later passes.

    A cost accountant must use independently reviewed, operation-bound billing
    evidence. This API does not estimate an actual charge from a reservation.
    """
    if type(max_operations) is not int or not 1 <= max_operations <= 64:
        raise V2Conflict("recovery pass needs a bounded operation limit")
    if not callable(cost_accountant):
        raise V2Conflict("independently verified cost accountant required")

    state = ledger.status(job_id)
    stage = next((s for s in state["stages"] if s["name"] == "chirp_independent"), None)
    if stage is None:
        raise V2Conflict("missing Chirp stage")
    if stage["state"] == "COMPLETE":
        merged = job_dir / "golden-v2" / "chirp-merged.json"
        if (not merged.is_file() or
                file_sha256(merged) != stage["evidence_sha"]):
            raise V2Conflict("completed Chirp evidence missing or changed")
        return {"job_id": job_id, "action": "ALREADY_COMPLETE",
                "recovered": 0, "pending": [], "unknown": [],
                "provider_submissions": 0, "drive_mutations": 0}
    allowed_state = (
        (stage["state"] == "RUNNING" and state["status"] == "PROCESSING")
        or (stage["state"] == "OVER_BUDGET"
            and state["status"] == "NEEDS_RECONCILIATION")
    )
    if not allowed_state:
        raise V2Conflict("Chirp recovery requires an approved active or frozen stage")

    rows = ledger.chirp_windows(job_id)
    if not rows:
        raise V2Conflict("no immutable Chirp plan exists")

    recovered = 0
    quarantined = 0
    reconciled = 0
    inspected = 0
    recovery_errors: list[dict[str, str]] = []
    for row in rows:
        if row["status"] != "SUBMITTED" or inspected >= max_operations:
            continue
        inspected += 1
        try:
            status = recover_chirp_window(
                ledger=ledger, job_id=job_id, window_id=row["window_id"],
                job_dir=job_dir, gateway=gateway, cost_accountant=cost_accountant,
            )
        except V2Conflict as exc:
            # Isolate a bad/temporarily unavailable operation. Continue through
            # the bounded batch so one response cannot strand other saved LROs.
            recovery_errors.append({"window_id": row["window_id"], "reason": str(exc)})
            continue
        if status == "DONE":
            recovered += 1
        elif status == "REVIEW_REQUIRED":
            quarantined += 1

    # A newer parser may safely resolve prior raw-only quarantines. This is a
    # local evidence operation: it performs no provider request and preserves
    # the already reconciled billed amount.
    for row in rows:
        if row["status"] != "REVIEW_REQUIRED" or inspected >= max_operations:
            continue
        inspected += 1
        try:
            status = reconcile_reviewed_chirp_window(
                ledger=ledger, job_id=job_id, window_id=row["window_id"],
                job_dir=job_dir,
            )
        except V2Conflict as exc:
            recovery_errors.append({"window_id": row["window_id"], "reason": str(exc)})
            continue
        if status == "DONE":
            reconciled += 1

    current = ledger.chirp_windows(job_id)
    pending = [r["window_id"] for r in current if r["status"] == "SUBMITTED"]
    unknown = [r["window_id"] for r in current if r["status"] == "SUBMITTING"]
    unsubmitted = [r["window_id"] for r in current if r["status"] == "PLANNED"]
    review_required = [r["window_id"] for r in current
                       if r["status"] == "REVIEW_REQUIRED"]
    error_ids = {item["window_id"] for item in recovery_errors}
    retryable_pending = [window_id for window_id in pending if window_id not in error_ids]
    result: dict[str, Any] = {
        "job_id": job_id, "recovered": recovered, "quarantined": quarantined,
        "reconciled": reconciled,
        "inspected": inspected,
        "pending": pending, "unknown": unknown, "unsubmitted": unsubmitted,
        "review_required": review_required, "recovery_errors": recovery_errors,
        "provider_submissions": 0, "drive_mutations": 0,
    }

    current_job = ledger.status(job_id)
    stage_actual = next((int(s["actual_cents"] or 0) for s in current_job["stages"]
                         if s["name"] == "chirp_independent"), 0)
    other_actual = sum(int(s["actual_cents"] or 0) for s in current_job["stages"]
                       if s["name"] != "chirp_independent")
    chirp_actual = sum(int(row["actual_cents"] or 0) for row in current)
    result["chirp_known_actual_cents"] = max(stage_actual, chirp_actual)
    result["known_actual_cents"] = other_actual + result["chirp_known_actual_cents"]
    if current_job["status"] == "NEEDS_RECONCILIATION":
        result["action"] = "COST_RECONCILIATION_REQUIRED"
    elif unknown:
        result["action"] = "UNKNOWN_OPERATION_REQUIRES_REVIEW"
    elif retryable_pending:
        result["action"] = "WAITING_FOR_EXISTING_OPERATIONS"
    elif recovery_errors:
        result["action"] = "RECOVERY_BLOCKED_RETRY_OR_REVIEW"
    elif review_required:
        result["action"] = "WINDOW_REVIEW_REQUIRED"
    elif unsubmitted:
        result["action"] = "APPROVED_SUBMISSION_WORKER_REQUIRED"
    elif all(r["status"] == "DONE" for r in current):
        merged = finish_approved_chirp(
            ledger=ledger, job_id=job_id, job_dir=job_dir,
            acoustic_inspector=acoustic_inspector,
        )
        result["word_count"] = len(merged["words"])
        result["acoustic_gate"] = merged["acoustic_gate"]
        result["acoustic_qa_sha256"] = merged.get("acoustic_qa_sha256")
        if merged["acoustic_gate"] == "PASS":
            result["action"] = "CHIRP_COMPLETE"
        elif merged.get("repair_plan", {}).get("intervals"):
            result["action"] = "STANDARD_REPAIR_APPROVAL_REQUIRED"
            result["repair_interval_count"] = len(merged["repair_plan"]["intervals"])
            result["manual_review_required"] = merged.get("manual_review_required", True)
        else:
            result["action"] = "MANUAL_ACOUSTIC_REVIEW_REQUIRED"
            result["manual_review_required"] = True
    else:
        raise V2Conflict("unexpected Chirp recovery state")
    return result


def create_production_chirp_gateway(*, project: str, dynamic_batching: bool = True) -> Any:
    """Construct a real Speech V2/GCS adapter; makes no API call itself.

    Only a separately approved submission path may invoke gateway.submit().
    """
    from google.cloud import speech_v2, storage
    from app.providers.golden_v2_chirp_gateway import ChirpV2Gateway

    return ChirpV2Gateway(
        speech_client=speech_v2.SpeechClient(
            client_options={"api_endpoint": "us-speech.googleapis.com"},
        ),
        storage_client=storage.Client(project=project),
        project=project, dynamic_batching=dynamic_batching,
    )
