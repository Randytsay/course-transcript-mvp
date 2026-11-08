"""Golden V2 final artifact gate; refuses auto-publication without evidence.

This does not make model decisions or mutate Drive. Existing V1 SRT editor
and safe publisher remain the only supported downstream publication paths.
"""
from __future__ import annotations

import re
from typing import Any
import unicodedata

from app.subtitles.editor_hardened import parse_srt_strict


def _text_without_layout(text: str) -> str:
    # Remove only display whitespace; preserve actual Chinese punctuation,
    # numbers, canonical scripture characters, and model spelling.
    return "".join(ch for ch in unicodedata.normalize("NFC", text) if not ch.isspace())


def validate_golden_v2(
    *, srt: str, transcript: str,
    chirp_word_boundaries_ms: set[int],
    acoustic_gate: str,
    semantic_gate: str,
    comparison: dict[str, Any],
    adjudications: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        cues, _ = parse_srt_strict(srt)
    except Exception:
        cues = []
        errors.append("invalid_srt")
    if not cues:
        errors.append("no_cues")
    if cues:
        cue_text = "".join(str(cue["corrected_text"]) for cue in cues)
        if _text_without_layout(cue_text) != _text_without_layout(transcript):
            errors.append("srt_txt_mismatch")
        if not chirp_word_boundaries_ms:
            errors.append("missing_chirp_timing_evidence")
        else:
            for cue in cues:
                if cue["start_ms"] not in chirp_word_boundaries_ms or cue["end_ms"] not in chirp_word_boundaries_ms:
                    errors.append("cue_without_chirp_word_boundary")
                    break
    if acoustic_gate != "PASS":
        errors.append("acoustic_completeness_not_passed")
    if semantic_gate != "PASS":
        errors.append("semantic_review_not_passed")
    candidates = comparison.get("candidates") or []
    resolution = {
        int(entry["candidate_index"]): entry for entry in adjudications
        if isinstance(entry, dict) and isinstance(entry.get("candidate_index"), int)
    }
    for index, candidate in enumerate(candidates):
        entry = resolution.get(index) or {}
        if (
            entry.get("status") != "RESOLVED"
            or not entry.get("audio_evidence_checked")
            or not str(entry.get("decision_reason") or "").strip()
        ):
            errors.append(f"unresolved_comparison_candidate:{index}")
    if comparison.get("acoustic_coverage") == "PASS" and acoustic_gate != "PASS":
        errors.append("contradictory_acoustic_gate")
    errors = sorted(set(errors))
    return {
        "schema": "golden-v2-final-gate-v1",
        "status": "PASS" if not errors else "BLOCKED",
        "errors": errors,
        "cue_count": len(cues),
        "comparison_candidate_count": len(candidates),
        "resolved_candidate_count": len(candidates) if not any(
            error.startswith("unresolved_comparison_candidate") for error in errors
        ) else None,
        "safe_publish_allowed": not errors,
    }
