"""Conservative subtitle-boundary and punctuation integrity checks.

The scanner is intentionally review-only.  It does not rewrite transcript
content.  High-confidence lexical splits are inferred from the same lesson:
a Han phrase must occur intact elsewhere often enough, while one occurrence is
split across a near-continuous cue boundary with no terminal punctuation.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any

_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_PUNCTUATION = set("，。！？；：、,.!?;:")
_STRONG_TERMINAL = set("。！？?!；;")
_REPEATED_PUNCT_RE = re.compile(r"[,，。！？?!；;:：、]{2,}")

# Grammatical glue frequently crosses cue boundaries without forming one
# lexical item.  Excluding it keeps the detector conservative.
_GLUE_PREFIXES = {
    "的", "地", "得", "把", "被", "在", "從", "往", "向", "對", "跟",
    "和", "與", "為", "到", "給", "讓", "將", "以", "於", "是", "有",
    "就", "再", "還", "也", "都", "而", "或", "但",
}
_INTERNAL_GLUE = set("的地得把被在從往向對跟和與為到給讓將以於是有就再還也都而或但")


def _text(segment: dict[str, Any]) -> str:
    return str(
        segment.get("cleaned_text")
        or segment.get("corrected_text")
        or segment.get("raw_text")
        or segment.get("text")
        or ""
    )


def _han(text: str) -> str:
    return "".join(_HAN_RE.findall(text))


def _intact_frequencies(segments: list[dict[str, Any]]) -> Counter[str]:
    frequencies: Counter[str] = Counter()
    for segment in segments:
        text = _han(_text(segment))
        for length in range(2, 7):
            for index in range(0, len(text) - length + 1):
                frequencies[text[index:index + length]] += 1
    return frequencies


def _minimum_intact_count(length: int) -> int:
    if length == 2:
        return 10
    if length == 3:
        return 5
    return 2


def scan_boundary_integrity(
    segments: list[dict[str, Any]],
    *,
    max_gap_ms: int = 500,
) -> dict[str, Any]:
    frequencies = _intact_frequencies(segments)
    leading_punctuation: list[dict[str, Any]] = []
    repeated_punctuation: list[dict[str, Any]] = []
    close_unpunctuated_boundaries: list[dict[str, Any]] = []
    split_candidates: list[dict[str, Any]] = []

    for segment in segments:
        text = _text(segment).strip()
        if not text:
            continue
        if text[0] in _PUNCTUATION:
            leading_punctuation.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "start_ms": int(segment.get("start_ms") or 0),
                    "text": text,
                }
            )
        if _REPEATED_PUNCT_RE.search(text):
            repeated_punctuation.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "start_ms": int(segment.get("start_ms") or 0),
                    "text": text,
                }
            )

    for before, after in zip(segments, segments[1:]):
        before_text = _text(before).strip()
        after_text = _text(after).strip()
        if not before_text or not after_text:
            continue
        gap_ms = int(after.get("start_ms") or 0) - int(before.get("end_ms") or 0)
        if gap_ms < 0 or gap_ms > max_gap_ms:
            continue
        if before_text[-1] in _PUNCTUATION:
            continue
        before_han = _han(before_text)
        after_han = _han(after_text)
        if not before_han or not after_han:
            continue

        entry = {
            "before_segment_id": before.get("segment_id"),
            "after_segment_id": after.get("segment_id"),
            "boundary_ms": int(before.get("end_ms") or 0),
            "gap_ms": gap_ms,
            "before_text": before_text,
            "after_text": after_text,
        }
        close_unpunctuated_boundaries.append(entry)

        matches: list[dict[str, Any]] = []
        for left_size in range(1, min(4, len(before_han)) + 1):
            for right_size in range(1, min(4, len(after_han)) + 1):
                term = before_han[-left_size:] + after_han[:right_size]
                length = len(term)
                if not 2 <= length <= 6:
                    continue
                if len(set(term)) == 1:
                    continue
                intact_count = int(frequencies.get(term, 0))
                if intact_count < _minimum_intact_count(length):
                    continue
                left_piece = before_han[-left_size:]
                # A very frequent two-character word may legitimately begin
                # with a grammatical character (e.g. 往生).  Longer phrases
                # containing grammatical glue are usually syntactic spans,
                # not one lexical item (e.g. 佛的智慧), so keep them review-noise-free.
                if left_piece in _GLUE_PREFIXES and not (
                    length == 2 and intact_count >= 12
                ):
                    continue
                if length >= 3 and any(char in _INTERNAL_GLUE for char in term):
                    continue
                matches.append(
                    {
                        "term": term,
                        "intact_elsewhere_count": intact_count,
                        "left_piece": left_piece,
                        "right_piece": after_han[:right_size],
                    }
                )

        if matches:
            matches.sort(
                key=lambda item: (
                    int(item["intact_elsewhere_count"]) * len(str(item["term"])),
                    int(item["intact_elsewhere_count"]),
                    len(str(item["term"])),
                ),
                reverse=True,
            )
            best = matches[0]
            split_candidates.append({**entry, **best})

    return {
        "version": "boundary-integrity-v1",
        "segment_count": len(segments),
        "max_gap_ms": max_gap_ms,
        "close_unpunctuated_boundary_count": len(close_unpunctuated_boundaries),
        "close_unpunctuated_boundaries": close_unpunctuated_boundaries,
        "split_candidate_count": len(split_candidates),
        "split_candidates": split_candidates,
        "leading_punctuation_count": len(leading_punctuation),
        "leading_punctuation": leading_punctuation,
        "repeated_punctuation_count": len(repeated_punctuation),
        "repeated_punctuation": repeated_punctuation,
    }
