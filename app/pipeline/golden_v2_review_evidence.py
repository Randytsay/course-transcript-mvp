"""Review evidence gate for Golden V2 adjudication batches.

The 2026-10-22 audit found all 4,091 adjudications shared one reason string,
every timed review window was the mechanical ``[start-1000, end+1000]``
formula, and all 749 untimed candidates were given a fabricated audio window
they had no timestamp for. That is a rubber stamp, not a review.

This gate rejects a batch that shows those signatures. It never decides what
the correct subtitle is: it only refuses evidence that cannot have come from
actually listening to the original audio.

The mechanical-window rule needs care. A reviewer who genuinely listened to a
span will naturally write bounds near the disputed words, and a batch where
*every* window is the exact padded formula is the signature of a generator
rather than a listener. The rule therefore fires on a batch-wide ratio, not
on any single window.
"""
from __future__ import annotations

from typing import Any

# Padding a reviewer might plausibly apply by hand. A window that is exactly
# this formula for every candidate is machine-generated.
MECHANICAL_WINDOW_PADDING_MS = 1000

# Share of judged decisions whose windows are mechanically padded, above which
# the batch is treated as generated rather than reviewed. The three audited
# batches measured 90.7%, 88.6% and 100% on the combined anchor, and 100% on
# their timed candidates alone. 0.85 sits below all three and far above what
# a batch of individually chosen listening windows would produce.
MECHANICAL_WINDOW_RATIO = 0.85

# Do not judge window regularity on a handful of decisions.
MIN_DECISIONS_FOR_WINDOW_JUDGEMENT = 10

# A batch this large with a single shared reason string is a stamp, not a
# review. Small batches may legitimately share one summary reason.
UNIFORM_REASON_MIN_BATCH = 20


class ReviewEvidenceError(RuntimeError):
    """Raised when an adjudication batch cannot be accepted as a review."""


def _window_signature(candidate: dict[str, Any]) -> tuple[int, int] | None:
    start = candidate.get("source_time_start")
    end = candidate.get("source_time_end")
    if type(start) is not int or type(end) is not int:
        return None
    return start, end


def assess_review_evidence(
    *, candidates: list[dict[str, Any]], decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify an adjudication batch's evidentiary quality.

    Raises ``ReviewEvidenceError`` when the batch carries a rubber-stamp
    signature; otherwise returns a PASS report.
    """
    candidates = list(candidates or [])
    decisions = list(decisions or [])

    reasons: list[str] = []
    resolved = [entry for entry in decisions
                if isinstance(entry, dict) and entry.get("status") == "RESOLVED"]

    # 1. A single shared reason across a large batch is a stamp.
    #
    # The three audited batches each used exactly one reason string for
    # 453 / 472 / 3166 decisions. A single reason that covers an entire
    # large batch describes a batch, not the audio.
    reason_strings = {
        str(entry.get("decision_reason") or "").strip() for entry in resolved
    }
    reason_strings.discard("")
    distinct_reasons = len(reason_strings)
    if (len(resolved) >= UNIFORM_REASON_MIN_BATCH
            and distinct_reasons <= 1):
        reasons.append("uniform_decision_reason")

    # 2. Mechanically padded windows, and 3. windows that anchor to nothing.
    #
    # The audited batches used a single formula, [anchor-1000, anchor+1000],
    # where anchor is the candidate's own span when it has one and the
    # adjacent Chirp context when it does not. 97.7% of all 4,091 decisions
    # matched it exactly. A window chosen by listening is not a formula.
    timed = 0
    untimed = 0
    mechanical = 0
    unanchored = 0
    for entry in resolved:
        index = entry.get("candidate_index")
        if type(index) is not int or not 0 <= index < len(candidates):
            continue
        candidate = candidates[index]
        window = entry.get("reviewed_audio_window_ms")
        if not (isinstance(window, list) and len(window) == 2
                and all(type(value) is int for value in window)):
            continue
        span = _window_signature(candidate)
        if span is None:
            # No timestamp of its own: the window must at least bracket the
            # adjacent Chirp words, or it refers to no known audio at all.
            context = candidate.get("adjacent_chirp_context") or {}
            left = context.get("left_chirp_word_ms")
            right = context.get("right_chirp_word_ms")
            if type(left) is not int or type(right) is not int:
                unanchored += 1
                continue
            untimed += 1
            span = (left, right)
            if not (window[0] <= left and window[1] >= right):
                unanchored += 1
        else:
            timed += 1
        start, end = span
        if window == [start - MECHANICAL_WINDOW_PADDING_MS,
                      end + MECHANICAL_WINDOW_PADDING_MS]:
            mechanical += 1

    judged = timed + untimed
    if (judged >= MIN_DECISIONS_FOR_WINDOW_JUDGEMENT
            and mechanical / judged >= MECHANICAL_WINDOW_RATIO):
        reasons.append("mechanical_review_window")
    if unanchored:
        reasons.append("unanchored_review_window")

    report = {
        "schema": "golden-v2-review-evidence-v1",
        "status": "BLOCKED" if reasons else "PASS",
        "reasons": sorted(set(reasons)),
        "resolved_decision_count": len(resolved),
        "distinct_decision_reasons": distinct_reasons,
        "timed_decision_count": timed,
        "untimed_decision_count": untimed,
        "mechanical_window_count": mechanical,
        "unanchored_window_count": unanchored,
        "mechanical_window_padding_ms": MECHANICAL_WINDOW_PADDING_MS,
    }
    if reasons:
        raise ReviewEvidenceError(
            "adjudication evidence is not a genuine audio review: "
            + ", ".join(report["reasons"]))
    return report


def require_real_review_evidence(
    *, candidates: list[dict[str, Any]], decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the evidence report, raising when the batch is not a review."""
    return assess_review_evidence(candidates=candidates, decisions=decisions)
