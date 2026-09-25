from __future__ import annotations

"""Bidirectional transcript completeness screening.

This module only reports candidates. It never edits source ASR, reviewed text,
or timestamps. A source-heavy gap means the human transcript may have omitted
speech. A human-heavy gap means the ASR/source may have omitted speech.
"""

import bisect
import difflib
import re
from typing import Any


OMISSION_DETECTOR_VERSION = "bidirectional-v1"


def _normalize(value: str) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", value or "")


def detect_bidirectional_omissions(
    source_segments: list[dict[str, Any]],
    human_text: str,
    *,
    anchor_chars: int = 8,
    min_gap_chars: int = 45,
    max_other_ratio: float = 0.20,
    min_source_gap_ms: int = 12_000,
) -> dict[str, Any]:
    ranges: list[tuple[int, int, int]] = []
    parts: list[str] = []
    cursor = 0
    for index, segment in enumerate(source_segments):
        text = _normalize(
            str(
                segment.get("cleaned_text")
                or segment.get("corrected_text")
                or segment.get("text")
                or segment.get("raw_text")
                or ""
            )
        )
        parts.append(text)
        ranges.append((cursor, cursor + len(text), index))
        cursor += len(text)
    source_text = "".join(parts)
    human_normalized = _normalize(human_text)
    starts = [item[0] for item in ranges]

    def char_to_segment(char_pos: int) -> int:
        index = bisect.bisect_right(starts, max(0, int(char_pos))) - 1
        return max(0, min(index, len(ranges) - 1))

    def char_to_time(char_pos: int, *, end: bool = False) -> int:
        segment_index = char_to_segment(char_pos)
        char_start, char_end, _ = ranges[segment_index]
        segment = source_segments[segment_index]
        start_ms = int(segment.get("start_ms") or 0)
        end_ms = max(start_ms + 1, int(segment.get("end_ms") or start_ms + 1))
        if char_end <= char_start:
            return end_ms if end else start_ms
        probe = max(char_start, min(char_end, int(char_pos)))
        return round(
            start_ms
            + (end_ms - start_ms) * (probe - char_start) / (char_end - char_start)
        )

    if not source_text or not human_normalized:
        return {
            "schema_version": 1,
            "detector_version": OMISSION_DETECTOR_VERSION,
            "mode": "report_only",
            "candidates": [],
            "candidate_count": 0,
        }

    matcher = difflib.SequenceMatcher(None, source_text, human_normalized, autojunk=False)
    anchors = [block for block in matcher.get_matching_blocks() if block.size >= anchor_chars]
    candidates: list[dict[str, Any]] = []
    for left, right in zip(anchors, anchors[1:]):
        source_start = left.a + left.size
        source_end = right.a
        human_start = left.b + left.size
        human_end = right.b
        source_gap = source_end - source_start
        human_gap = human_end - human_start
        if source_gap < 0 or human_gap < 0:
            continue
        start_ms = char_to_time(source_start)
        end_ms = char_to_time(source_end, end=True)
        source_duration_ms = max(0, end_ms - start_ms)

        direction = ""
        if (
            source_gap >= min_gap_chars
            and source_duration_ms >= min_source_gap_ms
            and human_gap <= max(12, int(source_gap * max_other_ratio))
        ):
            direction = "human_missing"
        elif (
            human_gap >= min_gap_chars
            and source_gap <= max(12, int(human_gap * max_other_ratio))
        ):
            direction = "source_missing"
        if not direction:
            continue

        candidates.append(
            {
                "direction": direction,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_gap_chars": source_gap,
                "human_gap_chars": human_gap,
                "source_duration_ms": source_duration_ms,
                "left_anchor": source_text[left.a : left.a + left.size],
                "right_anchor": source_text[right.a : right.a + right.size],
                "source_excerpt": source_text[source_start:source_end][:300],
                "human_excerpt": human_normalized[human_start:human_end][:300],
                "review_required": True,
            }
        )

    return {
        "schema_version": 1,
        "detector_version": OMISSION_DETECTOR_VERSION,
        "mode": "report_only",
        "source_modified": False,
        "human_modified": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
