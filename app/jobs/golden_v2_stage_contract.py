"""Golden V2 pure stage/approval contract.

This is a framework for the future persistent Worker, not a substitute for the
existing SQLite lease, cost ledger, raw-provider evidence, or safe-publish.
It cannot trigger a paid request or mutate Drive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

QUALITY_3WAY = "QUALITY_3WAY"
SMART_2PLUS = "SMART_2PLUS"
GEMINI_CHIRP = "GEMINI_CHIRP"
MODES = frozenset({QUALITY_3WAY, SMART_2PLUS, GEMINI_CHIRP})

# The first Gemini full-transcript gate *must precede* Chirp submission.
STAGES = (
    "inventory",
    "preflight",
    "gemini_primary",
    "gemini_primary_completeness",
    "chirp_independent",
    "chirp_acoustic_gate",
    "transcribe_optional",
    "cross_compare",
    "webcodex_adjudication",
    "chirp_timing_alignment",
    "golden_qa",
    "export",
    "safe_publish",
    "published_verified",
)


@dataclass(frozen=True)
class V2Selection:
    source_sha256: str
    mode: str = QUALITY_3WAY
    model_primary: str = "gemini-3.8-flash"
    model_chirp: str = "chirp_3"
    model_transcribe: str = "gemini-3.5-transcribe"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", self.source_sha256):
            raise ValueError("A fixed valid source SHA256 is required")
        if self.mode not in MODES:
            raise ValueError("Unsupported Golden V2 mode")
        if (self.model_primary, self.model_chirp, self.model_transcribe) != (
            "gemini-3.8-flash", "chirp_3", "gemini-3.5-transcribe"
        ):
            raise ValueError("Models must be pinned; do not silently switch a running job")

    @property
    def transcribe_policy(self) -> str:
        return (
            "ALL" if self.mode == QUALITY_3WAY else
            "RISK_WINDOWS" if self.mode == SMART_2PLUS else "SKIP"
        )


def next_stage(current: str | None, *, transcribe_policy: str) -> str | None:
    if transcribe_policy not in {"ALL", "RISK_WINDOWS", "SKIP"}:
        raise ValueError("Unrecognized transcribe policy")
    if current is None:
        return STAGES[0]
    try:
        i = STAGES.index(current)
    except ValueError as exc:
        raise ValueError("Unknown V2 stage") from exc
    if i + 1 >= len(STAGES):
        return None
    # Skipping third-party calls is a first-class *recorded* stage decision.
    # Do not drop the transcribe_optional stage from the persisted history.
    return STAGES[i + 1]


def assert_stage_can_advance(
    *,
    completed_stage: str,
    next_requested: str,
    evidence: dict,
) -> None:
    """Reject missing gate evidence and out-of-order provider calls."""
    expected = next_stage(completed_stage, transcribe_policy="ALL")
    if next_requested != expected:
        raise ValueError(f"Cannot jump from {completed_stage} to {next_requested}")
    if completed_stage == "gemini_primary_completeness" and not evidence.get("primary_candidate_complete"):
        raise ValueError("Gemini primary completion gate has not passed")
    if completed_stage == "chirp_acoustic_gate" and not evidence.get("acoustic_gate_pass"):
        raise ValueError("Chirp acoustic gate is not clear")
    if completed_stage == "golden_qa" and not evidence.get("golden_qa_pass"):
        raise ValueError("Golden QA blockers remain")


def authorize_v2_cost(
    *,
    approval_reference: str | None,
    source_sha256: str,
    approved_source_sha256: str,
    reserved_usd: str,
    estimated_increment_usd: str,
    spent_usd: str,
    test_budget_cap_usd: str = "10",
) -> None:
    """New V2 test cap is independent of the V1 auto-approve threshold.

    This validates caller-provided reservation evidence ONLY. Worker must still
    verify reference against owner-signed/DB approval and durable ledger.
    """
    if not approval_reference or not approval_reference.strip():
        raise PermissionError("No source-scoped owner approval")
    if source_sha256.lower() != approved_source_sha256.lower() or not re.fullmatch(
        r"[a-f0-9]{64}", source_sha256.lower()
    ):
        raise PermissionError("Approval does not cover this source")
    try:
        reserve, addition, spent, cap = map(
            Decimal, (reserved_usd, estimated_increment_usd, spent_usd, test_budget_cap_usd)
        )
    except InvalidOperation as exc:
        raise PermissionError("Invalid cost ledger") from exc
    if not all(x.is_finite() and x >= 0 for x in (reserve, addition, spent, cap)):
        raise PermissionError("Cost ledger contains invalid amounts")
    if addition == 0 or spent + addition > reserve or spent + addition > cap:
        raise PermissionError("Requested paid call exceeds approved V2 reserve/cap")
