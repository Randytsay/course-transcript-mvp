from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

_TEXT_KEY_RE = re.compile(r"[^\u3400-\u9fffA-Za-z0-9]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；])")
_SCRIPTURE_CUE_SPLIT_RE = re.compile(r"(?<=[。！？；：，])")


def text_key(value: str) -> str:
    return _TEXT_KEY_RE.sub("", str(value or "")).lower()


def canonical_lines(body_text: str) -> list[str]:
    body = str(body_text or "").replace("\r\n", "\n").strip()
    if not body:
        return []
    explicit = [line.strip() for line in body.splitlines() if line.strip()]
    if len(explicit) > 1:
        return explicit
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(body) if part.strip()]


def canonical_scripture_units(body_text: str) -> list[str]:
    units: list[str] = []
    for line in canonical_lines(body_text):
        # CBETA TEI extraction can legitimately leave stand-alone closing
        # quotation marks (for example '』') on their own logical line.
        # They have no searchable characters after text_key normalization
        # and therefore cannot own a source-timing span. Dropping them here
        # keeps display units and normalized spans in lockstep.
        parts = [
            part.strip()
            for part in _SCRIPTURE_CUE_SPLIT_RE.split(line)
            if part.strip() and text_key(part)
        ]
        if not parts:
            continue
        index = 0
        while index < len(parts):
            part = parts[index]
            # Very short lead-ins such as「爾時，」「是時，」「今者，」are
            # not useful standalone subtitle cues. Keep them with the following
            # semantic clause while preserving longer comma-delimited clauses.
            if (
                len(text_key(part)) <= 3
                and part.endswith("，")
                and index + 1 < len(parts)
            ):
                units.append(part + parts[index + 1])
                index += 2
                continue
            units.append(part)
            index += 1
    return units


def _source_char_to_ms(
    segments: list[dict[str, Any]],
    spans: list[tuple[int, int, int]],
    char_index: int,
    *,
    end: bool = False,
) -> int:
    segment_index = _segment_index_for_char(spans, char_index, end=end)
    if segment_index is None:
        return 0
    span = next(
        (item for item in spans if item[2] == segment_index),
        None,
    )
    segment = segments[segment_index]
    start_ms = int(segment.get("start_ms") or 0)
    end_ms = max(start_ms + 1, int(segment.get("end_ms") or start_ms + 1))
    if span is None:
        return end_ms if end else start_ms
    span_start, span_end, _ = span
    span_len = max(1, span_end - span_start)
    probe = max(span_start, min(span_end, int(char_index)))
    ratio = (probe - span_start) / span_len
    return round(start_ms + (end_ms - start_ms) * ratio)


def _map_target_char_to_source(
    blocks: list[Any],
    target_char: int,
) -> int:
    anchors = [block for block in blocks if int(block.size) > 0]
    if not anchors:
        return 0
    target_char = max(0, int(target_char))
    for block in anchors:
        block_start = int(block.b)
        block_end = block_start + int(block.size)
        if block_start <= target_char <= block_end:
            return int(block.a) + min(int(block.size), target_char - block_start)
    before = [block for block in anchors if int(block.b) + int(block.size) <= target_char]
    after = [block for block in anchors if int(block.b) >= target_char]
    if before and after:
        left = before[-1]
        right = after[0]
        left_target = int(left.b) + int(left.size)
        left_source = int(left.a) + int(left.size)
        right_target = int(right.b)
        right_source = int(right.a)
        target_span = max(1, right_target - left_target)
        fraction = (target_char - left_target) / target_span
        return round(left_source + (right_source - left_source) * fraction)
    if before:
        left = before[-1]
        return int(left.a) + int(left.size) + max(
            0,
            target_char - (int(left.b) + int(left.size)),
        )
    right = after[0]
    return max(0, int(right.a) - (int(right.b) - target_char))


def _joined_source(
    segments: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, int, int]]]:
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, item in enumerate(segments):
        key = text_key(str(item.get("cleaned_text") or item.get("corrected_text") or ""))
        if not key:
            continue
        start = cursor
        parts.append(key)
        cursor += len(key)
        spans.append((start, cursor, index))
    return "".join(parts), spans


def _segment_index_for_char(
    spans: list[tuple[int, int, int]], char_index: int, *, end: bool = False
) -> int | None:
    if not spans:
        return None
    probe = max(0, int(char_index) - (1 if end and char_index > 0 else 0))
    for start, stop, index in spans:
        if start <= probe < stop:
            return index
    return spans[-1][2] if probe >= spans[-1][1] else spans[0][2]


def _matching_evidence(source: str, target: str, min_anchor: int) -> dict[str, Any]:
    if not source or not target:
        return {
            "matched_chars": 0,
            "anchor_count": 0,
            "anchor_units": 0,
            "source_start_char": None,
            "source_end_char": None,
            "target_start_char": None,
            "target_end_char": None,
            "source_coverage": 0.0,
            "target_coverage": 0.0,
        }
    matcher = SequenceMatcher(None, source, target, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size >= min_anchor]
    if not blocks:
        return {
            "matched_chars": 0,
            "anchor_count": 0,
            "anchor_units": 0,
            "source_start_char": None,
            "source_end_char": None,
            "target_start_char": None,
            "target_end_char": None,
            "source_coverage": 0.0,
            "target_coverage": 0.0,
        }
    matched = sum(block.size for block in blocks)
    anchor_units = sum(max(1, block.size // max(1, min_anchor)) for block in blocks)
    source_start = min(block.a for block in blocks)
    source_end = max(block.a + block.size for block in blocks)
    target_start = min(block.b for block in blocks)
    target_end = max(block.b + block.size for block in blocks)
    source_span = max(1, source_end - source_start)
    target_span = max(1, target_end - target_start)
    return {
        "matched_chars": matched,
        "anchor_count": len(blocks),
        "anchor_units": anchor_units,
        "source_start_char": source_start,
        "source_end_char": source_end,
        "target_start_char": target_start,
        "target_end_char": target_end,
        "source_coverage": round(matched / source_span, 4),
        "target_coverage": round(matched / max(1, len(target)), 4),
        "target_span_coverage": round(matched / target_span, 4),
    }


def _canonical_line_spans(lines: list[str]) -> tuple[str, list[tuple[int, int, int]]]:
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


def scripture_display_layer(
    cleaned: list[dict[str, Any]],
    canonical: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    display = [{**item, "source_segment_ids": [str(item["segment_id"])]} for item in cleaned]
    if canonical is None or not str(canonical.get("body_text") or "").strip():
        return display, {
            "applied": False,
            "reason": "canonical_scripture_missing",
            "review_required": True,
        }

    total_end_ms = max((int(item.get("end_ms") or 0) for item in cleaned), default=0)
    scan_cap_ms = int(os.environ.get("SCRIPTURE_SCAN_MAX_MS", "1200000"))
    scan_fraction = float(os.environ.get("SCRIPTURE_SCAN_MAX_FRACTION", "0.30"))
    scan_end_ms = min(scan_cap_ms, max(300_000, int(total_end_ms * scan_fraction)))
    early = [item for item in cleaned if int(item.get("start_ms") or 0) <= scan_end_ms]
    if not early:
        return display, {
            "applied": False,
            "reason": "scripture_alignment_review",
            "review_required": True,
            "detail": "no_early_segments",
        }

    lines = canonical_scripture_units(str(canonical.get("body_text") or ""))
    canonical_text, canonical_spans = _canonical_line_spans(lines)
    source_text, source_spans = _joined_source(early)
    min_anchor = int(os.environ.get("SCRIPTURE_MIN_EXACT_ANCHOR_CHARS", "6"))
    evidence = _matching_evidence(source_text, canonical_text, min_anchor)
    min_chars = int(os.environ.get("SCRIPTURE_MIN_EXACT_MATCH_CHARS", "90"))
    min_anchors = int(os.environ.get("SCRIPTURE_MIN_EXACT_ANCHORS", "8"))
    min_source_coverage = float(os.environ.get("SCRIPTURE_MIN_SOURCE_COVERAGE", "0.32"))
    max_start_ms = int(os.environ.get("SCRIPTURE_START_MAX_MS", "600000"))
    if (
        int(evidence["matched_chars"]) < min_chars
        or int(evidence["anchor_units"]) < min_anchors
        or float(evidence["source_coverage"]) < min_source_coverage
        or evidence["source_start_char"] is None
        or evidence["target_start_char"] is None
    ):
        return display, {
            "applied": False,
            "reason": "scripture_alignment_review",
            "review_required": True,
            "canonical_version": canonical.get("active_version"),
            "canonical_checksum": canonical.get("active_checksum"),
            "evidence": evidence,
        }

    first_source = _segment_index_for_char(source_spans, int(evidence["source_start_char"]))
    last_source = _segment_index_for_char(source_spans, int(evidence["source_end_char"]), end=True)
    first_line = _segment_index_for_char(canonical_spans, int(evidence["target_start_char"]))
    last_line = _segment_index_for_char(canonical_spans, int(evidence["target_end_char"]), end=True)
    if None in {first_source, last_source, first_line, last_line}:
        return display, {
            "applied": False,
            "reason": "scripture_alignment_review",
            "review_required": True,
            "detail": "alignment_span_unresolved",
            "evidence": evidence,
        }
    assert first_source is not None and last_source is not None
    assert first_line is not None and last_line is not None
    if int(early[first_source].get("start_ms") or 0) > max_start_ms:
        return display, {
            "applied": False,
            "reason": "scripture_alignment_review",
            "review_required": True,
            "detail": "matched_block_starts_too_late",
            "evidence": evidence,
        }

    selected_lines = lines[first_line : last_line + 1]
    if len(selected_lines) < 3:
        return display, {
            "applied": False,
            "reason": "scripture_alignment_review",
            "review_required": True,
            "detail": "too_few_canonical_lines",
            "evidence": evidence,
        }
    source_start_ms = int(early[first_source].get("start_ms") or 0)
    source_end_ms = int(early[last_source].get("end_ms") or source_start_ms + 1)
    selected_source_segments = early[first_source : last_source + 1]
    selected_source_text, selected_source_spans = _joined_source(selected_source_segments)
    selected_target_text, selected_target_spans = _canonical_line_spans(selected_lines)
    matcher = SequenceMatcher(
        None,
        selected_source_text,
        selected_target_text,
        autojunk=False,
    )
    blocks = matcher.get_matching_blocks()
    canonical_cues: list[dict[str, Any]] = []
    previous_end = source_start_ms
    for offset, line in enumerate(selected_lines):
        span = selected_target_spans[offset]
        target_start_char = span[0]
        target_end_char = span[1]
        mapped_source_start = _map_target_char_to_source(blocks, target_start_char)
        mapped_source_end = _map_target_char_to_source(blocks, target_end_char)
        cue_start = _source_char_to_ms(
            selected_source_segments,
            selected_source_spans,
            mapped_source_start,
            end=False,
        )
        cue_end = _source_char_to_ms(
            selected_source_segments,
            selected_source_spans,
            mapped_source_end,
            end=True,
        )
        cue_start = max(source_start_ms, min(source_end_ms - 1, cue_start))
        cue_end = max(cue_start + 1, min(source_end_ms, cue_end))
        if cue_start < previous_end:
            cue_start = previous_end
        if cue_end <= cue_start:
            cue_end = min(source_end_ms, cue_start + 1)
        previous_end = cue_end
        source_first_for_cue = _segment_index_for_char(
            selected_source_spans,
            mapped_source_start,
        )
        source_last_for_cue = _segment_index_for_char(
            selected_source_spans,
            mapped_source_end,
            end=True,
        )
        if source_first_for_cue is None:
            source_first_for_cue = 0
        if source_last_for_cue is None:
            source_last_for_cue = source_first_for_cue
        source_first_for_cue = max(0, source_first_for_cue)
        source_last_for_cue = min(
            len(selected_source_segments) - 1,
            max(source_first_for_cue, source_last_for_cue),
        )
        source_ids = [
            str(item["segment_id"])
            for item in selected_source_segments[
                source_first_for_cue : source_last_for_cue + 1
            ]
        ]
        text = str(line).strip().lstrip("\u300c\u300e").rstrip("\u300f\u300d").strip()
        canonical_cues.append(
            {
                **selected_source_segments[source_first_for_cue],
                "segment_id": f"scripture-display-{offset + 1:04d}",
                "start_ms": cue_start,
                "end_ms": max(cue_start + 1, cue_end),
                "raw_text": text,
                "corrected_text": text,
                "cleaned_text": text,
                "source_segment_ids": source_ids,
                "cleanup_actions": ["scripture_display_canonicalized"],
                "cleanup_review_reasons": [],
            }
        )
    # Source indices are indices in `early`, which is a prefix of `cleaned`.
    display = [*display[:first_source], *canonical_cues, *display[last_source + 1 :]]
    return display, {
        "applied": True,
        "match": "ordered_exact_text_anchors",
        "review_required": False,
        "canonical_version": canonical.get("active_version"),
        "canonical_checksum": canonical.get("active_checksum"),
        "canonical_line_start": first_line + 1,
        "canonical_line_end": last_line + 1,
        "source_start_segment_id": early[first_source]["segment_id"],
        "source_end_segment_id": early[last_source]["segment_id"],
        "source_cue_count": last_source - first_source + 1,
        "publication_layer": "display_segments",
        "evidence": evidence,
    }


_MANTRA_CLOSING_RE = re.compile(
    r"(?:大眾.{0,5}(?:起|立)|向.{0,12}問訊|問訊|法會.{0,8}(?:圓滿|完滿|結滿))"
)
_MANTRA_TITLE_RE = re.compile(r"彌勒.{0,10}根本.{0,10}(?:明|密).{0,6}咒")


def _phrase_units(
    display: list[dict[str, Any]], start_index: int, end_index: int
) -> list[dict[str, Any]]:
    """Treat each subtitle cue as one spoken unit.

    Do not split punctuation here. Canonical mantra lines themselves can contain
    punctuation and intrinsic repetition. A cue such as X，X is detected by the
    self-pair matcher, while X followed by X is an adjacent pair.
    """
    units: list[dict[str, Any]] = []
    for display_index in range(start_index, end_index):
        item = display[display_index]
        source_text = str(item.get("cleaned_text") or item.get("corrected_text") or "").strip()
        key = text_key(source_text)
        if not key:
            continue
        start_ms = int(item.get("start_ms") or 0)
        end_ms = max(start_ms + 1, int(item.get("end_ms") or start_ms + 1))
        units.append(
            {
                "display_index": display_index,
                "segment_id": str(item.get("segment_id")),
                "text": source_text,
                "key": key,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
    return units


def _phrase_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _self_pair_similarity(value: str) -> float:
    if len(value) < 8:
        return 0.0
    best = 0.0
    midpoint = len(value) // 2
    for split in range(max(4, midpoint - 2), min(len(value) - 3, midpoint + 2) + 1):
        left, right = value[:split], value[split:]
        if min(len(left), len(right)) < 4:
            continue
        best = max(best, _phrase_similarity(left, right))
    return best


def _pair_group_at(
    units: list[dict[str, Any]], position: int, threshold: float
) -> dict[str, Any] | None:
    if position >= len(units):
        return None
    first = units[position]
    first_key = str(first["key"])
    if position + 1 < len(units):
        second = units[position + 1]
        second_key = str(second["key"])
        similarity = _phrase_similarity(first_key, second_key)
        if min(len(first_key), len(second_key)) >= 4 and similarity >= threshold:
            return {
                "unit_start": position,
                "unit_end": position + 1,
                "next_position": position + 2,
                "similarity": round(similarity, 4),
                "exact": first_key == second_key,
                "adjacent_pair": True,
                "base_key": first_key,
            }
    self_similarity = _self_pair_similarity(first_key)
    self_threshold = max(
        float(os.environ.get("MANTRA_SELF_PAIR_MIN_SIMILARITY", "0.90")), threshold
    )
    if self_similarity >= self_threshold:
        return {
            "unit_start": position,
            "unit_end": position,
            "next_position": position + 1,
            "similarity": round(self_similarity, 4),
            "exact": self_similarity == 1.0,
            "adjacent_pair": False,
            "base_key": first_key[: max(1, len(first_key) // 2)],
        }
    return None


def _parse_mantra_cycle(
    units: list[dict[str, Any]], start: int, line_count: int, threshold: float
) -> dict[str, Any] | None:
    groups: list[dict[str, Any]] = []
    position = start
    for _ in range(line_count):
        group = _pair_group_at(units, position, threshold)
        if group is None:
            return None
        groups.append(group)
        position = int(group["next_position"])
    similarities = [float(group["similarity"]) for group in groups]
    return {
        "unit_start": start,
        "unit_end": position - 1,
        "next_position": position,
        "groups": groups,
        "exact_pair_count": sum(bool(group["exact"]) for group in groups),
        "adjacent_pair_count": sum(bool(group["adjacent_pair"]) for group in groups),
        "min_similarity": round(min(similarities), 4),
        "average_similarity": round(sum(similarities) / len(similarities), 4),
        "first_key": str(groups[0]["base_key"]),
    }


def _cycle_is_strong(cycle: dict[str, Any], line_count: int) -> bool:
    min_exact = int(
        os.environ.get("MANTRA_CYCLE_MIN_EXACT_PAIRS", str(max(8, line_count - 5)))
    )
    min_adjacent = int(
        os.environ.get("MANTRA_CYCLE_MIN_ADJACENT_PAIRS", str(max(6, line_count // 3)))
    )
    min_average = float(
        os.environ.get("MANTRA_CYCLE_MIN_AVG_PAIR_SIMILARITY", "0.88")
    )
    return (
        int(cycle["exact_pair_count"]) >= min_exact
        and int(cycle["adjacent_pair_count"]) >= min_adjacent
        and float(cycle["average_similarity"]) >= min_average
    )


def _find_mantra_cycle_chain(
    units: list[dict[str, Any]], line_count: int
) -> list[dict[str, Any]]:
    threshold = float(os.environ.get("MANTRA_PAIR_MIN_REPEAT_SIMILARITY", "0.62"))
    restart_threshold = float(
        os.environ.get("MANTRA_CYCLE_RESTART_SIMILARITY", "0.72")
    )
    max_trailing_units = int(
        os.environ.get("MANTRA_CYCLE_MAX_TRAILING_UNITS", "2")
    )
    candidates: list[list[dict[str, Any]]] = []
    for start in range(len(units)):
        first = _parse_mantra_cycle(units, start, line_count, threshold)
        if first is None or not _cycle_is_strong(first, line_count):
            continue
        chain = [first]
        position = int(first["next_position"])
        while position < len(units):
            next_cycle = _parse_mantra_cycle(units, position, line_count, threshold)
            if next_cycle is None or not _cycle_is_strong(next_cycle, line_count):
                break
            if (
                _phrase_similarity(
                    str(first["first_key"]), str(next_cycle["first_key"])
                )
                < restart_threshold
            ):
                break
            chain.append(next_cycle)
            position = int(next_cycle["next_position"])
        if len(units) - position <= max_trailing_units:
            candidates.append(chain)
    if not candidates:
        return []
    candidates.sort(
        key=lambda chain: (
            len(chain),
            sum(int(cycle["exact_pair_count"]) for cycle in chain),
            sum(int(cycle["adjacent_pair_count"]) for cycle in chain),
            -int(chain[0]["unit_start"]),
        ),
        reverse=True,
    )
    return candidates[0]


def _title_replacement_start(
    units: list[dict[str, Any]], first_unit: int, default_index: int
) -> int:
    if first_unit <= 0:
        return default_index
    lookback = units[max(0, first_unit - 4) : first_unit]
    title_units = [
        unit
        for unit in lookback
        if _MANTRA_TITLE_RE.search(str(unit.get("text") or ""))
    ]
    if not title_units:
        return default_index
    return min(default_index, *(int(unit["display_index"]) for unit in title_units))


def mantra_pair_display_layer(
    display: list[dict[str, Any]],
    canonical: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if canonical is None or not str(canonical.get("body_text") or "").strip():
        return display, {
            "applied": False,
            "reason": "canonical_mantra_missing",
            "review_required": True,
        }
    lines = canonical_lines(str(canonical.get("body_text") or ""))
    if not lines:
        return display, {
            "applied": False,
            "reason": "canonical_mantra_missing",
            "review_required": True,
        }

    # Publication proof is structural, not phonetic.  The real ritual pattern is
    # leader X -> congregation X for every canonical line.  We verify a complete
    # 17-pair cycle (and any whole-cycle repetitions) from the ASR's own repeated
    # phrases, then use only backend canonical text for the emitted display layer.
    search_floor = max(
        0,
        len(display) - int(os.environ.get("MANTRA_CLOSING_SCAN_CUES", "320")),
    )
    closing_index = next(
        (
            index
            for index in range(search_floor, len(display))
            if _MANTRA_CLOSING_RE.search(
                str(display[index].get("cleaned_text") or "")
            )
        ),
        None,
    )
    if closing_index is None:
        return display, {
            "applied": False,
            "reason": "leader_congregation_pair_sequence_review",
            "review_required": True,
            "canonical_version": canonical.get("active_version"),
            "canonical_checksum": canonical.get("active_checksum"),
            "expected_pair_count": len(lines),
            "detail": "closing_ritual_cue_not_found",
        }

    region_start = max(
        0,
        closing_index - int(os.environ.get("MANTRA_PAIR_SCAN_CUES", "260")),
    )
    all_units = _phrase_units(display, region_start, closing_index)
    title_positions = [
        index
        for index, unit in enumerate(all_units)
        if _MANTRA_TITLE_RE.search(str(unit.get("text") or ""))
    ]
    last_title_position = max(title_positions) if title_positions else None
    mantra_unit_offset = (last_title_position + 1) if last_title_position is not None else 0
    units = all_units[mantra_unit_offset:]
    chain = _find_mantra_cycle_chain(units, len(lines))
    title_evidence = bool(
        chain
        and last_title_position is not None
        and int(chain[0]["unit_start"]) <= 6
    )
    if not chain or (len(chain) < 2 and not title_evidence):
        return display, {
            "applied": False,
            "reason": "leader_congregation_pair_sequence_review",
            "review_required": True,
            "canonical_version": canonical.get("active_version"),
            "canonical_checksum": canonical.get("active_checksum"),
            "expected_pair_count": len(lines),
            "detail": (
                "no_strong_structural_17_pair_cycle"
                if not chain
                else "single_cycle_without_mantra_title_evidence"
            ),
            "evidence": {
                "phrase_unit_count": len(all_units),
                "closing_cue_segment_id": display[closing_index].get("segment_id"),
                "title_evidence": title_evidence,
                "cycle_count": len(chain),
            },
        }

    first_group = chain[0]["groups"][0]
    last_group = chain[-1]["groups"][-1]
    first_unit = int(first_group["unit_start"])
    last_unit = int(last_group["unit_end"])
    source_first_index = int(units[first_unit]["display_index"])
    source_last_index = int(units[last_unit]["display_index"])
    replacement_first = source_first_index
    if title_evidence and last_title_position is not None:
        title_window = all_units[max(0, last_title_position - 3) : last_title_position + 1]
        title_display_indices = [
            int(unit["display_index"])
            for unit in title_window
            if _MANTRA_TITLE_RE.search(str(unit.get("text") or ""))
        ]
        if title_display_indices:
            replacement_first = min(source_first_index, *title_display_indices)

    cues: list[dict[str, Any]] = []
    for cycle_index, cycle in enumerate(chain):
        for line_index, (line, group) in enumerate(zip(lines, cycle["groups"])):
            unit_start = int(group["unit_start"])
            unit_end = int(group["unit_end"])
            selected_units = units[unit_start : unit_end + 1]
            source_ids = list(
                dict.fromkeys(str(unit["segment_id"]) for unit in selected_units)
            )
            cue_start = int(selected_units[0]["start_ms"])
            cue_end = int(selected_units[-1]["end_ms"])
            text = (
                f"{canonical.get('title')}\n{line}"
                if cycle_index == 0 and line_index == 0
                else line
            )
            cues.append(
                {
                    **display[int(selected_units[0]["display_index"])],
                    "segment_id": (
                        f"mantra-display-c{cycle_index + 1:02d}-"
                        f"l{line_index + 1:02d}"
                    ),
                    "start_ms": cue_start,
                    "end_ms": max(cue_start + 1, cue_end),
                    "raw_text": text,
                    "corrected_text": text,
                    "cleaned_text": text,
                    "source_segment_ids": source_ids,
                    "cleanup_actions": [
                        "mantra_leader_congregation_pair_folded"
                    ],
                    "cleanup_review_reasons": [],
                }
            )

    cycle_count = len(chain)
    exact_pair_count = sum(int(cycle["exact_pair_count"]) for cycle in chain)
    adjacent_pair_count = sum(
        int(cycle["adjacent_pair_count"]) for cycle in chain
    )
    similarities = [
        float(group["similarity"])
        for cycle in chain
        for group in cycle["groups"]
    ]
    return [
        *display[:replacement_first],
        *cues,
        *display[source_last_index + 1 :],
    ], {
        "applied": True,
        "match": "leader_congregation_structural_pair_cycles",
        "review_required": False,
        "canonical_version": canonical.get("active_version"),
        "canonical_checksum": canonical.get("active_checksum"),
        "canonical_line_count": len(lines),
        "cycle_count": cycle_count,
        "input_pair_count": len(lines) * cycle_count,
        "output_line_count": len(lines) * cycle_count,
        "exact_pair_count": exact_pair_count,
        "adjacent_pair_count": adjacent_pair_count,
        "minimum_pair_similarity": round(min(similarities), 4),
        "average_pair_similarity": round(
            sum(similarities) / len(similarities), 4
        ),
        "whole_cycles_preserved": True,
        "leader_congregation_duplicate_removed": True,
        "title_evidence": title_evidence,
        "source_start_segment_id": display[replacement_first]["segment_id"],
        "source_end_segment_id": display[source_last_index]["segment_id"],
        "source_cue_count": source_last_index - replacement_first + 1,
        "closing_cue_segment_id": display[closing_index].get("segment_id"),
        "publication_layer": "display_segments",
        "evidence": {
            "phrase_unit_count": len(all_units),
            "cycle_count": cycle_count,
            "exact_pair_count": exact_pair_count,
            "adjacent_pair_count": adjacent_pair_count,
        },
    }
