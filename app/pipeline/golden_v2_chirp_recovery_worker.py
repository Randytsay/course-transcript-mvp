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
)
from app.providers.golden_v2_audio_staging import file_sha256


def run_chirp_recovery_pass(
    *, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
    gateway: Any, cost_accountant: Callable[[dict[str, Any]], int],
    max_operations: int = 8,
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
    if (stage["state"] != "RUNNING" or
            state["status"] not in {"PROCESSING", "NEEDS_RECONCILIATION"}):
        raise V2Conflict("Chirp recovery requires an active approved stage")

    rows = ledger.chirp_windows(job_id)
    if not rows:
        raise V2Conflict("no immutable Chirp plan exists")

    recovered = 0
    inspected = 0
    for row in rows:
        if row["status"] != "SUBMITTED" or inspected >= max_operations:
            continue
        inspected += 1
        status = recover_chirp_window(
            ledger=ledger, job_id=job_id, window_id=row["window_id"],
            job_dir=job_dir, gateway=gateway, cost_accountant=cost_accountant,
        )
        if status == "DONE":
            recovered += 1

    current = ledger.chirp_windows(job_id)
    pending = [r["window_id"] for r in current if r["status"] == "SUBMITTED"]
    unknown = [r["window_id"] for r in current if r["status"] == "SUBMITTING"]
    unsubmitted = [r["window_id"] for r in current if r["status"] == "PLANNED"]
    result: dict[str, Any] = {
        "job_id": job_id, "recovered": recovered, "inspected": inspected,
        "pending": pending, "unknown": unknown, "unsubmitted": unsubmitted,
        "provider_submissions": 0, "drive_mutations": 0,
    }

    current_job = ledger.status(job_id)
    if current_job["status"] == "NEEDS_RECONCILIATION":
        result["action"] = "COST_RECONCILIATION_REQUIRED"
    elif unknown:
        result["action"] = "UNKNOWN_OPERATION_REQUIRES_REVIEW"
    elif pending:
        result["action"] = "WAITING_FOR_EXISTING_OPERATIONS"
    elif unsubmitted:
        result["action"] = "APPROVED_SUBMISSION_WORKER_REQUIRED"
    elif all(r["status"] == "DONE" for r in current):
        merged = finish_approved_chirp(ledger=ledger, job_id=job_id, job_dir=job_dir)
        result["action"] = "CHIRP_COMPLETE"
        result["word_count"] = len(merged["words"])
        result["acoustic_gate"] = merged["acoustic_gate"]
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
