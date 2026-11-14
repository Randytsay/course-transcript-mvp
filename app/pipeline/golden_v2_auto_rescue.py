"""Read-only auto-rescue eligibility for a verified Gemini SAFETY refusal.

Provider-free: never constructs a provider client, performs network IO,
approves costs, writes the ledger, or mutates job artifacts. The worker pass
attaches the eligibility outcome; a separate paid-side hook consumes it and
invokes the existing operator rescue script. This module never starts a paid
request itself, and an eligible outcome is not permission to charge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_primary_rescue import verify_primary_refusal

ELIGIBLE_REASON = "VERIFIED_SAFETY_REFUSAL"


def inspect_auto_rescue_eligibility(
    *, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
) -> dict[str, Any]:
    """Return whether a held job may auto-claim an independent Chirp rescue.

    Read-only. Eligible only when the job is NEEDS_REVIEW in FLASH_CHIRP
    mode with a REVIEW_REQUIRED Gemini primary, no Chirp operation exists
    yet, and verify_primary_refusal accepts the persisted refusal evidence.
    Every other state fails closed with an explicit reason.
    """
    job_dir = Path(job_dir)
    try:
        state = ledger.status(job_id)
    except (V2Conflict, OSError, ValueError, KeyError) as exc:
        return {"eligible": False, "reason": "LEDGER_STATUS_UNAVAILABLE",
                "detail": str(exc)}
    stages = {row["name"]: row for row in state.get("stages", [])}
    primary = stages.get("gemini_primary")
    if state.get("status") != "NEEDS_REVIEW":
        return {"eligible": False, "reason": "JOB_STATUS_NOT_NEEDS_REVIEW",
                "status": state.get("status")}
    if state.get("mode") != "FLASH_CHIRP":
        return {"eligible": False, "reason": "MODE_NOT_FLASH_CHIRP",
                "mode": state.get("mode")}
    if primary is None or primary.get("state") != "REVIEW_REQUIRED":
        return {"eligible": False, "reason": "PRIMARY_NOT_REVIEW_REQUIRED",
                "primary_state": primary.get("state") if primary else None}
    try:
        if ledger.chirp_windows(job_id):
            return {"eligible": False,
                    "reason": "EXISTING_CHIRP_OPERATION_RECONCILE"}
    except (V2Conflict, OSError, ValueError) as exc:
        return {"eligible": False, "reason": "CHIRP_OPERATIONS_UNREADABLE",
                "detail": str(exc)}
    if primary.get("evidence_sha") is None or primary.get("actual_cents") is None:
        return {"eligible": False, "reason": "PRIMARY_EVIDENCE_INCOMPLETE"}
    try:
        verify_primary_refusal(
            job_dir=job_dir, job_id=job_id, source_sha=state["source_sha"],
            evidence_sha=primary["evidence_sha"],
            actual_cents=primary["actual_cents"],
        )
    except (V2Conflict, OSError, ValueError, KeyError, TypeError) as exc:
        return {"eligible": False, "reason": "PRIMARY_REFUSAL_NOT_VERIFIED",
                "detail": str(exc)}
    return {
        "eligible": True,
        "reason": ELIGIBLE_REASON,
        "stage": "gemini_primary",
        "source_sha256": state["source_sha"],
        "approved_cents": state.get("approved_cents"),
        "revision": state.get("revision"),
        "primary_actual_cents": primary["actual_cents"],
    }
