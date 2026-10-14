"""Provider-free bounded textual comparison grounded only in Chirp word times.

Long recordings use unique monotonic exact-text anchors. No Gemini timecodes
are generated, and ambiguous spans require acoustic/manual review.
"""
from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
import unicodedata
from typing import Any


class AlignmentNeedsReview(RuntimeError):
    pass


def _letters(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(c.casefold() for c in normalized if unicodedata.category(c)[0] in {"L", "N"})


def _anchors(source: str, target: str, limit: int) -> list[tuple[int, int, int]]:
    """Accept only unique exact strings occurring in increasing source order."""
    if max(len(source), len(target)) <= limit or source == target:
        return []
    anchor_size = min(32, max(8, limit // 8))
    step = max(1, limit // 3)
    anchors: list[tuple[int, int, int]] = []
    last_a = last_b = 0
    for guess in range(step, len(source) - anchor_size + 1, step):
        for a in range(max(last_a, guess - step // 2),
                       min(len(source) - anchor_size + 1, guess + step // 2),
                       max(1, anchor_size // 2)):
            phrase = source[a:a + anchor_size]
            if source.find(phrase) != source.rfind(phrase):
                continue
            b = target.find(phrase)
            if b < last_b or target.rfind(phrase) != b:
                continue
            if max(a - last_a, b - last_b) > limit:
                continue
            anchors.append((a, b, anchor_size))
            last_a, last_b = a + anchor_size, b + anchor_size
            break
    return anchors


def compare_gemini_chirp(
    gemini_text: str, chirp_words: list[dict[str, Any]], *,
    max_characters: int = 12000, source_sha256: str | None = None,
) -> dict[str, Any]:
    """Return review candidates with source_time_* as real Chirp milliseconds.

    Insertions and unanchored spans receive null times; other times cite word
    hashes. Textual matching is never an acoustic-completeness assertion.
    """
    if not gemini_text.strip() or not chirp_words or max_characters < 64:
        raise AlignmentNeedsReview("empty evidence or invalid comparison bound")
    source_chars: list[str] = []
    word_by_char: list[int] = []
    last_end = -1
    for i, word in enumerate(chirp_words):
        start, end = int(word["start_ms"]), int(word["end_ms"])
        if start < 0 or end <= start or start < last_end:
            raise AlignmentNeedsReview("invalid or nonmonotonic Chirp timing")
        last_end = end
        for ch in _letters(str(word.get("word", ""))):
            source_chars.append(ch)
            word_by_char.append(i)
    source = "".join(source_chars)
    target = _letters(gemini_text)
    if not source or not target:
        raise AlignmentNeedsReview("no comparable spoken characters")

    def candidate(tag: str, a0: int, a1: int, b0: int, b1: int, *,
                  bounded: bool) -> dict[str, Any]:
        indices = sorted({word_by_char[i] for i in range(a0, a1)}) if bounded else []
        times = ({
            "chirp_start_ms": chirp_words[indices[0]]["start_ms"],
            "chirp_end_ms": chirp_words[indices[-1]]["end_ms"],
        } if indices else None)
        left = word_by_char[a0-1] if a0 else None
        right = word_by_char[a0] if a0 < len(word_by_char) else None
        evidence_ids = [
            hashlib.sha256((
                f"{source_sha256 or ''}|{i}|{chirp_words[i]['word']}|"
                f"{chirp_words[i]['start_ms']}|{chirp_words[i]['end_ms']}"
            ).encode()).hexdigest() for i in indices
        ]
        return {
            "kind": {"insert": "GEMINI_UNSUPPORTED_ADDITION",
                     "delete": "GEMINI_POSSIBLE_OMISSION",
                     "replace": "TEXT_DISAGREEMENT"}.get(tag, "UNANCHORED_LONG_SPAN"),
            "comparison_status": "REVIEW_REQUIRED",
            "chirp_char_range": [a0, a1],
            "gemini_char_range": [b0, b1],
            "chirp_normalized_excerpt": source[a0:a1][:500],
            "gemini_normalized_excerpt": target[b0:b1][:500],
            "source_time_start": times["chirp_start_ms"] if times else None,
            "source_time_end": times["chirp_end_ms"] if times else None,
            "source_time_unit": "milliseconds",
            "chirp_evidence_ids": evidence_ids,
            "chirp_timing_evidence": times,
            "adjacent_chirp_context": {
                "left_chirp_word_ms": chirp_words[left]["end_ms"] if left is not None else None,
                "right_chirp_word_ms": chirp_words[right]["start_ms"] if right is not None else None,
            },
            "gemini_timing_evidence": None,
            "auto_replacement_allowed": False,
        }

    candidates: list[dict[str, Any]] = []
    anchors = _anchors(source, target, max_characters)
    matching = compared = 0
    a0 = b0 = 0
    for a1, b1, skip in [*anchors, (len(source), len(target), 0)]:
        part_a, part_b = source[a0:a1], target[b0:b1]
        if part_a == part_b:
            matching += len(part_a)
            compared += len(part_a)
        elif max(len(part_a), len(part_b)) > max_characters:
            candidates.append(candidate("unanchored", a0, a1, b0, b1, bounded=False))
            compared += max(len(part_a), len(part_b))
        else:
            matcher = SequenceMatcher(None, part_a, part_b, autojunk=False)
            compared += max(len(part_a), len(part_b))
            for tag, x0, x1, y0, y1 in matcher.get_opcodes():
                if tag == "equal":
                    matching += x1 - x0
                else:
                    candidates.append(candidate(tag, a0+x0, a0+x1, b0+y0, b0+y1,
                                                bounded=True))
        a0, b0 = a1 + skip, b1 + skip
        matching += skip
        compared += skip
    return {
        "kind": "golden_v2_primary_chirp_comparison",
        "source_sha256": source_sha256,
        "acoustic_coverage": "NOT_DETERMINED_BY_TEXT_DIFF",
        "exact_normalized_match": not candidates and source == target,
        "similarity_ratio_not_accuracy": matching / compared if compared else 0,
        "pending_review_count": len(candidates), "candidates": candidates,
        "unaligned_long_spans": sum(c["kind"] == "UNANCHORED_LONG_SPAN" for c in candidates),
        "alignment_method": "unique_monotonic_anchors_with_bounded_local_diff",
        "chirp_timing_authority": True, "auto_publish_authorized": False,
    }
