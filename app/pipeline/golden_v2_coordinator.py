"""Single-pass, provider-free V2 coordinator for durable local stages.

The coordinator never authorizes cost, starts recognition, claims paid stages,
publishes Drive files, or accepts client-declared reviewer identities. Run it
again after a process restart; completed SQLite stages are reused verbatim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_stage_runner import (
    complete_adjudication_or_hold, complete_comparison,
    complete_evidence_bound_qa,
)


def advance_local(
    *, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
    worker_id: str = "golden-v2-local", minimum_recovery_age_seconds: int = 300,
) -> dict[str, Any]:
    """Perform at most one local stage, or return an explicit durable blocker.

    Recover only stale local RUNNING claims with revision CAS. Prior provider
    claims and every unknown paid outcome remain frozen and non-retryable.
    """
    state = ledger.status(job_id)
    stages = state["stages"]
    pending = next((row for row in stages if row["state"] not in
                    {"COMPLETE", "SKIPPED"}), None)
    if pending is None:
        return {"job_id": job_id, "status": state["status"],
                "action": "AWAITING_FINAL_REVIEW"}
    stage = pending["name"]
    if state["status"] not in {"QUEUED", "PROCESSING"}:
        return {"job_id": job_id, "stage": stage,
                "action": "BLOCKED", "reason": state["status"]}
    comparison_stage = next(row for row in stages if row["name"] == "compare")
    if stage in {"gemini_primary", "chirp_independent", "third_optional"}:
        can_skip_third = (
            stage == "third_optional" and pending["state"] == "PENDING"
            and (state["mode"] == "FLASH_CHIRP" or (
                state["mode"] == "SMART_2PLUS"
                and comparison_stage["state"] == "COMPLETE"
                and comparison_stage["review_required"] == 0
            ))
        )
        if not can_skip_third:
            return {"job_id": job_id, "stage": stage,
                    "action": "PROVIDER_STAGE_REQUIRED", "state": pending["state"]}
        # claim_next atomically skips the optional provider and then claims
        # adjudication. Leave disputed material to an authenticated reviewer.
        if comparison_stage["review_required"]:
            return {"job_id": job_id, "stage": "adjudication",
                    "action": "HUMAN_AUDIO_REVIEW_REQUIRED"}
    # Never generate an artificial human-review receipt for a discrepancy.
    if stage == "adjudication":
        if comparison_stage["review_required"]:
            return {"job_id": job_id, "stage": stage,
                    "action": "HUMAN_AUDIO_REVIEW_REQUIRED"}

    # The historical Chirp-first intake intentionally skipped Gemini.  The
    # ordinary comparison requires a COMPLETE, evidence-bound primary and
    # must not claim (or reclaim) compare until a separately audited
    # reference-alignment path is available.  Preserve existing claims.
    if stage == "compare" and stages[0]["state"] == "SKIPPED":
        return {"job_id": job_id, "stage": stage,
                "action": "CHIRP_FIRST_REFERENCE_ALIGN_REQUIRED",
                "state": pending["state"],
                "reason": "Gemini primary was skipped; standard comparison needs completed primary evidence"}

    if pending["state"] == "RUNNING":
        try:
            claim = ledger.reclaim_stale_local(
                job_id, stage=stage, worker_id=worker_id,
                minimum_age_seconds=minimum_recovery_age_seconds,
                expected_revision=state["revision"],
            )
        except V2Conflict as exc:
            return {"job_id": job_id, "stage": stage,
                    "action": "RECOVERY_BLOCKED", "reason": str(exc)}
    elif pending["state"] == "PENDING":
        claim = ledger.claim_next(job_id, worker_id=worker_id)
    else:
        return {"job_id": job_id, "stage": stage,
                "action": "BLOCKED", "reason": pending["state"]}

    if claim["stage"] == "compare":
        result = complete_comparison(ledger=ledger, claim=claim, job_dir=job_dir)
        action = "COMPARISON_COMPLETE"
    elif claim["stage"] == "adjudication":
        result = complete_adjudication_or_hold(
            ledger=ledger, claim=claim, job_dir=job_dir)
        action = "ADJUDICATION_COMPLETE"
    elif claim["stage"] == "golden_qa":
        result = complete_evidence_bound_qa(
            ledger=ledger, claim=claim, job_dir=job_dir)
        action = "QA_COMPLETE" if result["safe_publish_allowed"] else "QA_REVIEW_REQUIRED"
    else:
        raise V2Conflict("provider stage cannot be executed by local coordinator")
    return {"job_id": job_id, "stage": claim["stage"], "action": action,
            "status": ledger.status(job_id)["status"],
            "evidence_sha256": result.get("evidence_sha256"),
            "provider_calls_started": False, "drive_mutation_started": False}


def run_ready_local(*, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
                    worker_id: str = "golden-v2-local",
                    minimum_recovery_age_seconds: int = 300) -> list[dict[str, Any]]:
    """Bound execution to all (at most STAGES) currently ready local stages."""
    outcomes: list[dict[str, Any]] = []
    for _ in STAGES:
        outcome = advance_local(
            ledger=ledger, job_id=job_id, job_dir=job_dir,
            worker_id=worker_id,
            minimum_recovery_age_seconds=minimum_recovery_age_seconds,
        )
        outcomes.append(outcome)
        if outcome["action"] not in {
            "COMPARISON_COMPLETE", "ADJUDICATION_COMPLETE", "QA_COMPLETE",
        }:
            break
    return outcomes
