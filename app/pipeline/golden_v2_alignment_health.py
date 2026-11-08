"""Alignment health gate for Golden V2 comparison artifacts.

A comparison is only meaningful when the reference text is aligned to the
Chirp word timeline. Two failure modes produced the 4,091 bogus adjudications
audited on 2026-10-22:

* an incomplete reference (251025 covered 30.4% of the audio, 251018 20.4%),
  so most of the timeline had no second engine at all;
* a truncated reference (261017 lost ~5,168 characters), which shifted every
  downstream character offset by a constant, manufacturing ~1,775 phantom
  candidates.

Both are detectable before any human is asked to adjudicate, so the gate
refuses the comparison instead of letting it become thousands of decisions.

Textual alignment is never an acoustic-completeness assertion.
"""
from __future__ import annotations

from typing import Any

# A constant character offset at or above this size is not a wording
# disagreement; it means the two texts are not describing the same span.
LARGE_OFFSET_CHARS = 500

# A comparison whose reference covers less than this fraction of the audio
# cannot support a whole-file review.
MIN_PRIMARY_COVERAGE_RATIO = 0.98

# Only judge a systematic offset when there are enough candidates for the
# ratio to mean something.
MIN_CANDIDATES_FOR_OFFSET_JUDGEMENT = 20

# A systematic offset is a cascade when this share of candidates carry it.
SYSTEMATIC_OFFSET_RATIO = 0.5


class AlignmentHealthError(RuntimeError):
    """Raised when a comparison may not be adjudicated as-is."""


def _char_offset(candidate: dict[str, Any]) -> int | None:
    """Signed character distance between the Chirp and reference spans."""
    chirp = candidate.get("chirp_char_range")
    reference = candidate.get("gemini_char_range")
    if (not isinstance(chirp, list) or not isinstance(reference, list)
            or len(chirp) != 2 or len(reference) != 2):
        return None
    if any(type(value) is not int for value in (*chirp, *reference)):
        return None
    return reference[0] - chirp[0]


def assess_alignment_health(
    *, candidates: list[dict[str, Any]] | None,
    comparison: dict[str, Any] | None = None,
    primary_coverage_ratio: float | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Classify a comparison's alignment health.

    Returns a report with ``status`` of PASS or BLOCKED. ``BLOCKED`` lists
    machine-readable reasons; callers must refuse to adjudicate then.
    """
    candidates = list(candidates or [])
    reasons: list[str] = []
    total = len(candidates)

    offsets = [offset for offset in
               (_char_offset(candidate) for candidate in candidates)
               if offset is not None]
    large = [offset for offset in offsets if abs(offset) >= LARGE_OFFSET_CHARS]
    large_ratio = (len(large) / len(offsets)) if offsets else 0.0
    median_offset = 0
    if offsets:
        ordered = sorted(offsets)
        median_offset = ordered[len(ordered) // 2]

    if (primary_coverage_ratio is not None
            and primary_coverage_ratio < MIN_PRIMARY_COVERAGE_RATIO):
        reasons.append("incomplete_primary_coverage")

    if (total >= MIN_CANDIDATES_FOR_OFFSET_JUDGEMENT
            and large_ratio >= SYSTEMATIC_OFFSET_RATIO):
        reasons.append("systematic_reference_offset")

    report = {
        "schema": "golden-v2-alignment-health-v1",
        "status": "BLOCKED" if reasons else "PASS",
        "reasons": sorted(set(reasons)),
        "candidate_count": total,
        "offset_sampled_count": len(offsets),
        "large_offset_candidate_count": len(large),
        "large_offset_ratio": round(large_ratio, 4),
        "median_char_offset": median_offset,
        "primary_coverage_ratio": primary_coverage_ratio,
        "duration_ms": duration_ms,
        "large_offset_threshold_chars": LARGE_OFFSET_CHARS,
        "min_primary_coverage_ratio": MIN_PRIMARY_COVERAGE_RATIO,
        "textual_alignment_is_not_acoustic_evidence": True,
    }
    if comparison is not None:
        report["comparison_pending_review_count"] = comparison.get(
            "pending_review_count")
    return report


def require_healthy_alignment(**kwargs: Any) -> dict[str, Any]:
    """Return the health report, raising when the comparison is unusable."""
    report = assess_alignment_health(**kwargs)
    if report["status"] != "PASS":
        raise AlignmentHealthError(
            "comparison alignment is not trustworthy: "
            + ", ".join(report["reasons"]))
    return report
