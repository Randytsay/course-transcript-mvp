"""Golden V2 engine ordering contract; no production Worker hookup yet.

The caller owns durable approvals, actual provider runners, and all IO. This
orchestration function explicitly forbids Gemini text being passed to the
Chirp runner as recognition bias, preserves time authority, and never declares
Golden quality without acoustic/semantic QA.
"""
from __future__ import annotations

from typing import Any, Callable

from app.providers.gemini_primary_audio import assemble_windows
from app.providers.golden_v2_compare import compare_gemini_chirp

QUALITY = "QUALITY_3WAY"
SMART = "SMART_2PLUS"
TWO_ENGINE = "FLASH_CHIRP"
MODES = {QUALITY, SMART, TWO_ENGINE}


def evaluate_recognition(
    *,
    mode: str, staged_primary_windows: list[dict[str, Any]],
    duration_seconds: float,
    gemini_runner: Callable[[dict[str, Any]], dict[str, Any]],
    chirp_independent_runner: Callable[[], list[dict[str, Any]]],
    third_independent_runner: Callable[[], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Synchronous *testable* ordering; caller provides approved run callbacks.

    No provider calls, files, or external state are created by this function
    itself. Production must use resumable stage-ledger workers; it must not
    invoke this as an unbounded blocking web request.
    """
    if mode not in MODES:
        raise ValueError("unknown V2 engine mode")
    if mode == QUALITY and third_independent_runner is None:
        raise ValueError("quality mode requires third model runner")

    # Gemini is first and must structurally cover all staged source windows.
    primary_records = [gemini_runner(window) for window in staged_primary_windows]
    main = assemble_windows(staged_primary_windows, primary_records, duration_seconds)
    if not main["ready_for_chirp_comparison"]:
        raise ValueError("Gemini source-window overlap requires audio-guided reconciliation")

    # Chirp hears original audio without exposure to any Gemini candidate text.
    words = chirp_independent_runner()
    difference = compare_gemini_chirp(main["text"], words)

    third = {"status": "SKIPPED", "model": "gemini-3.5-transcribe"}
    should_run_third = mode == QUALITY or (
        mode == SMART and difference["pending_review_count"] > 0
    )
    if should_run_third:
        if third_independent_runner is None:
            # SMART must not silently omit an indicated review engine.
            raise ValueError("required third model runner not available")
        third = third_independent_runner()
        if third.get("status") != "COMPLETE":
            raise ValueError("third-model evidence incomplete")

    return {
        "schema": "golden-v2-recognition-flow-v1",
        "mode": mode,
        "model_order": ["gemini-3.8-flash", "chirp_3"] +
                       (["gemini-3.5-transcribe"] if should_run_third else []),
        "gemini_primary": main,
        "chirp_gemini_comparison": difference,
        "third_model": third,
        "chirp_words_count": len(words),
        "golden_approved": False,
        "next_stage": "CHATGPT_WEBCODEX_ADJUDICATION",
        "requires_acoustic_qa": True,
        "requires_semantic_qa": True,
    }
