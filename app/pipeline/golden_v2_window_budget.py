"""Durable cost checkpoints after *each* completed Golden V2 paid audio window.

Prevents a known overrun or unaccounted result from triggering another
billable request. Runs only after a provider response is already recorded;
the accounting function must derive real usage, not a placeholder estimate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_immutable import store_immutable_bytes


def check_paid_window_budget(
    *,
    ledger: GoldenV2Ledger,
    claim: dict[str, Any],
    job_dir: Path,
    completed_records: list[dict[str, Any]],
    accountant: Callable[[list[dict[str, Any]]], int],
) -> int:
    """Reconcile cumulative billed cents before permitting the NEXT window.

    A missing/invalid price reconciliation also freezes the stage. The
    immutable checkpoint references complete provider receipts; real unknown
    costs are never silently treated as zero-dollar charges.
    """
    if claim.get("stage") not in {"gemini_primary", "third_optional"}:
        raise V2Conflict("window budget gate only supports approved Gemini audio")
    if not completed_records or not callable(accountant):
        raise V2Conflict("completed provider receipts and accountant required")
    error: Exception | None = None
    try:
        cents = accountant(completed_records)
        if type(cents) is not int or cents < 0:
            raise ValueError("provider usage cannot be priced to integer USD cents")
    except Exception as exc:
        error = exc
        cents = 0

    if error is None and cents <= claim["estimate_cents"]:
        return cents

    payload = {
        "schema": "golden-v2-window-budget-hold-v1",
        "job_id": claim["job_id"],
        "stage": claim["stage"],
        "source_sha256": claim["source_sha"],
        "completed_window_receipts": completed_records,
        "completed_window_count": len(completed_records),
        "observed_cents": cents if error is None else None,
        "approved_stage_estimate_cents": claim["estimate_cents"],
        "reason": "UNPRICED_USAGE" if error is not None else "KNOWN_OVERRUN",
        "additional_paid_calls_allowed": False,
    }
    path = job_dir / "golden-v2" / (
        f"{claim['stage']}-window-cost-hold.json"
    )
    digest = store_immutable_bytes(
        path, json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8"),
    )
    ledger.mark_outcome_unknown(
        claim, known_cents=cents if error is None else 0,
        evidence_sha=digest,
    )
    raise V2Conflict(
        "provider usage unknown or exceeded approved stage budget; "
        "remaining audio windows blocked pending independent reconciliation"
    ) from error
