"""Golden V2 provider-free local integration proof.

This consumes prior *mock/fixture* transcript and Chirp evidence, does not
invoke providers, create persistent jobs, or write files/Drive. It is NOT the
production pipeline, and cannot grant Golden approval.
"""
from __future__ import annotations

from typing import Any

from app.jobs.golden_v2_stage_contract import V2Selection
from app.providers.engine_comparison_v2 import compare_primary_to_chirp
from app.subtitles.golden_v2_finalizer import render_checked_candidates


def preview_golden_v2(
    selection: V2Selection,
    *,
    gemini_primary: dict[str, Any],
    chirp_words: list[dict[str, Any]],
    candidate_cues: list[dict[str, Any]],
    transcribe_text: str | None = None,
) -> dict[str, Any]:
    """Fail closed; final candidate needs independent compare and cue approvals.

    This is a *local* proof of deterministic post-ASR semantics only. A PASS
    here must not bypass persisted audio coverage/VAD, user-approved budget,
    genuine final semantic adjudication, or safe publication transaction.
    """
    if gemini_primary.get("model") != selection.model_primary:
        raise ValueError("Wrong or missing Gemini primary model")
    if gemini_primary.get("source_sha256") != selection.source_sha256:
        raise ValueError("Gemini primary source identity mismatch")
    if not gemini_primary.get("primary_candidate_complete"):
        return {
            "status": "NEEDS_REVIEW",
            "reason": "gemini_primary_incomplete",
            "provider_calls": 0,
            "drive_mutations": 0,
            "golden_pass": False,
        }
    if selection.transcribe_policy == "ALL" and transcribe_text is None:
        return {
            "status": "NEEDS_REVIEW",
            "reason": "third_engine_missing",
            "provider_calls": 0,
            "drive_mutations": 0,
            "golden_pass": False,
        }
    comparison = compare_primary_to_chirp(
        primary_text=str(gemini_primary.get("text") or ""),
        chirp_words=chirp_words,
        transcribe_text=transcribe_text,
    )
    report: dict[str, Any] = {
        "status": "NEEDS_REVIEW",
        "reason": "engine_disagreements" if comparison["issue_count"] else "cue_review_required",
        "comparison": comparison,
        "provider_calls": 0,
        "drive_mutations": 0,
        "golden_pass": False,
    }
    if comparison["status"] != "MATCH":
        return report
    try:
        final = render_checked_candidates(candidate_cues)
    except ValueError as exc:
        report["reason"] = "cue_evidence_incomplete"
        report["error_type"] = type(exc).__name__
        return report
    report.update({
        "status": "STRUCTURAL_PASS_ONLY",
        "reason": "full_audio_vad_and_release_gates_not_run",
        "outputs": final,
    })
    return report
