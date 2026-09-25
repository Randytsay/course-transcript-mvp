from __future__ import annotations

import hashlib
import json
import os
import re
from difflib import SequenceMatcher
from typing import Any

from .alignment import canonical_scripture_units, text_key


_PUNCT_RE = re.compile(r"[\s，。！？；：、（）「」『』《》〈〉…—·]+")


def _joined(items: list[dict[str, Any]]) -> str:
    return "".join(
        text_key(str(item.get("raw_text") or item.get("corrected_text") or item.get("text") or ""))
        for item in items
    )


def _line_spans(lines: list[str]) -> tuple[str, list[tuple[int, int, int]]]:
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, line in enumerate(lines):
        key = text_key(line)
        if not key:
            continue
        start = cursor
        parts.append(key)
        cursor += len(key)
        spans.append((start, cursor, index))
    return "".join(parts), spans


def _span_index(spans: list[tuple[int, int, int]], char_index: int, *, end: bool = False) -> int | None:
    if not spans:
        return None
    probe = max(0, char_index - (1 if end and char_index > 0 else 0))
    for start, stop, index in spans:
        if start <= probe < stop:
            return index
    return spans[-1][2] if probe >= spans[-1][1] else spans[0][2]


def _anchors(source: str, target: str, min_anchor: int) -> dict[str, Any]:
    matcher = SequenceMatcher(None, source, target, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size >= min_anchor]
    if not blocks:
        return {
            "matched_chars": 0,
            "anchor_count": 0,
            "anchor_units": 0,
            "source_coverage": 0.0,
            "target_start_char": None,
            "target_end_char": None,
        }
    matched = sum(block.size for block in blocks)
    source_start = min(block.a for block in blocks)
    source_end = max(block.a + block.size for block in blocks)
    target_start = min(block.b for block in blocks)
    target_end = max(block.b + block.size for block in blocks)
    return {
        "matched_chars": matched,
        "anchor_count": len(blocks),
        "anchor_units": sum(max(1, block.size // max(1, min_anchor)) for block in blocks),
        "source_coverage": round(matched / max(1, source_end - source_start), 4),
        "target_start_char": target_start,
        "target_end_char": target_end,
    }


def _vocabulary(lines: list[str], limit: int = 120) -> list[str]:
    """Return bounded canonical phrases for spelling bias, not replacement."""
    seen: set[str] = set()
    values: list[str] = []
    for line in lines:
        compact = _PUNCT_RE.sub("", line)
        if not compact:
            continue
        # Prefer medium-length phrases: specific enough to be useful, bounded enough
        # to avoid flooding the prompt with the entire sutra as overlapping n-grams.
        for size in (6, 5, 4, 3, 2):
            for start in range(0, max(0, len(compact) - size + 1)):
                term = compact[start : start + size]
                if term in seen:
                    continue
                seen.add(term)
                values.append(term)
                if len(values) >= limit:
                    return values
    return values


def build_lesson_scripture_context(
    segments: list[dict[str, Any]],
    canonical: dict[str, Any] | None,
) -> dict[str, Any]:
    """Locate the opening recitation and build an auditable lesson-only context.

    This is intentionally conservative. A failed raw-ASR alignment produces no
    scripture bias; it never guesses a passage from phonetic similarity.
    """
    if canonical is None or not str(canonical.get("body_text") or "").strip():
        return {"applied": False, "reason": "canonical_scripture_missing", "review_required": True}
    if not segments:
        return {"applied": False, "reason": "no_segments", "review_required": True}

    total_end_ms = max(int(item.get("end_ms") or 0) for item in segments)
    scan_cap_ms = int(os.environ.get("SCRIPTURE_CONTEXT_SCAN_MAX_MS", "1200000"))
    scan_fraction = float(os.environ.get("SCRIPTURE_CONTEXT_SCAN_MAX_FRACTION", "0.30"))
    scan_end_ms = min(scan_cap_ms, max(300_000, int(total_end_ms * scan_fraction)))
    early = [item for item in segments if int(item.get("start_ms") or 0) <= scan_end_ms]
    source_text = _joined(early)
    lines = canonical_scripture_units(str(canonical.get("body_text") or ""))
    target_text, target_spans = _line_spans(lines)
    min_anchor = int(os.environ.get("SCRIPTURE_CONTEXT_MIN_EXACT_ANCHOR_CHARS", "5"))
    evidence = _anchors(source_text, target_text, min_anchor)
    if (
        int(evidence["matched_chars"]) < int(os.environ.get("SCRIPTURE_CONTEXT_MIN_EXACT_MATCH_CHARS", "54"))
        or int(evidence["anchor_units"]) < int(os.environ.get("SCRIPTURE_CONTEXT_MIN_EXACT_ANCHORS", "6"))
        or float(evidence["source_coverage"]) < float(os.environ.get("SCRIPTURE_CONTEXT_MIN_SOURCE_COVERAGE", "0.22"))
        or evidence["target_start_char"] is None
        or evidence["target_end_char"] is None
    ):
        return {
            "applied": False,
            "reason": "lesson_scripture_context_review",
            "review_required": True,
            "canonical_version": canonical.get("active_version"),
            "canonical_checksum": canonical.get("active_checksum"),
            "evidence": evidence,
        }

    first = _span_index(target_spans, int(evidence["target_start_char"]))
    last = _span_index(target_spans, int(evidence["target_end_char"]), end=True)
    if first is None or last is None or last < first:
        return {
            "applied": False,
            "reason": "lesson_scripture_context_review",
            "review_required": True,
            "detail": "canonical_span_unresolved",
            "evidence": evidence,
        }
    selected = lines[first : last + 1]
    if len(selected) < 3:
        return {
            "applied": False,
            "reason": "lesson_scripture_context_review",
            "review_required": True,
            "detail": "too_few_canonical_lines",
            "evidence": evidence,
        }
    payload = {
        "applied": True,
        "reason": "ordered_raw_asr_exact_anchors",
        "review_required": False,
        "alignment_confidence": (
            "high"
            if int(evidence["matched_chars"]) >= 90
            and int(evidence["anchor_units"]) >= 8
            and float(evidence["source_coverage"]) >= 0.32
            else "medium"
        ),
        "canonical_version": canonical.get("active_version"),
        "canonical_checksum": canonical.get("active_checksum"),
        "canonical_source": canonical.get("source") if isinstance(canonical.get("source"), dict) else {},
        "canonical_line_start": first + 1,
        "canonical_line_end": last + 1,
        "canonical_lines": selected,
        "vocabulary": _vocabulary(selected),
        "evidence": evidence,
    }
    payload["context_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def window_scripture_hint(items: list[dict[str, Any]], context: dict[str, Any] | None) -> dict[str, Any]:
    """Track the most likely sentence being explained in this correction window."""
    if not context or not context.get("applied"):
        return {"applied": False, "reason": "lesson_context_unavailable"}
    lines = [str(value) for value in context.get("canonical_lines", []) if str(value).strip()]
    if not lines:
        return {"applied": False, "reason": "lesson_context_empty"}
    source = _joined(items)
    if not source:
        return {"applied": False, "reason": "window_text_empty"}
    scored: list[tuple[float, int]] = []
    for index, line in enumerate(lines):
        key = text_key(line)
        if not key:
            continue
        ratio = SequenceMatcher(None, source, key, autojunk=False).ratio()
        longest = max((block.size for block in SequenceMatcher(None, source, key, autojunk=False).get_matching_blocks()), default=0)
        score = max(ratio, min(1.0, longest / 10.0))
        scored.append((score, index))
    if not scored:
        return {"applied": False, "reason": "no_scored_lines"}
    score, best = max(scored)
    start = max(0, best - 2)
    end = min(len(lines), best + 3)
    absolute_start = int(context.get("canonical_line_start") or 1)
    return {
        "applied": score >= float(os.environ.get("SCRIPTURE_EXPLANATION_TRACK_MIN_SCORE", "0.40")),
        "score": round(score, 4),
        "canonical_line": absolute_start + best,
        "neighborhood_line_start": absolute_start + start,
        "neighborhood_line_end": absolute_start + end - 1,
        "neighborhood": lines[start:end],
    }


def correction_reference_text(context: dict[str, Any] | None, hint: dict[str, Any] | None) -> str:
    if not context or not context.get("applied"):
        return ""
    selected = [str(value) for value in context.get("canonical_lines", []) if str(value).strip()]
    vocabulary = [str(value) for value in context.get("vocabulary", []) if str(value).strip()]
    parts = [
        "Lesson scripture context: the opening recitation was conservatively aligned to canonical lines "
        f"{context.get('canonical_line_start')}..{context.get('canonical_line_end')}. "
        f"Alignment confidence is {context.get('alignment_confidence', 'unknown')}. "
        "Use this passage only as spelling/terminology evidence for later explanations. "
        "Do not replace paraphrases, examples, or ordinary lecture speech with scripture wording.",
    ]
    if hint and hint.get("applied"):
        parts.append(
            "The current window most likely explains canonical line "
            f"{hint.get('canonical_line')} (tracking score {hint.get('score')}). "
            "Current local canonical neighborhood:\n"
            + "\n".join(str(value) for value in hint.get("neighborhood", []))
        )
    else:
        parts.append("Detected lesson passage:\n" + "\n".join(selected[:80]))
    if vocabulary:
        parts.append("Priority canonical vocabulary/spellings:\n" + "、".join(vocabulary[:120]))
    return "\n\n".join(parts)
