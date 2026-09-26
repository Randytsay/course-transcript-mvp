from __future__ import annotations

"""Deterministic review skill for 《佛說彌勒大成佛經》.

This module intentionally contains no provider client. It consumes an
existing SRT plus a human transcript and composes the already-established
canonical, Golden Corpus, Golden Rules, omission, mantra and QA primitives.
The current lesson is never written into learning state by this review path.
"""

from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from app.canonical.alignment import (
    canonical_lines,
    mantra_pair_display_layer,
    scripture_display_layer,
    text_key,
)
from app.canonical.defaults import MANTRA_KEY, MANTRA_TITLE, SCRIPTURE_KEY
from app.canonical.golden_corpus import (
    DEFAULT_CORPUS_RELATIVE_PATH,
    DEFAULT_ERROR_MEMORY_RELATIVE_PATH,
    corpus_digest,
    golden_corpus_reference,
    parse_srt_text,
)
from app.canonical.golden_rules import (
    GOLDEN_RULESET_VERSION,
    audit_golden_variants,
    golden_terms,
)
from app.canonical.omission_detection import detect_bidirectional_omissions
from app.canonical.store import active_canonical
from app.providers.boundary_integrity import scan_boundary_integrity


SKILL_VERSION = "dacheng-subtitle-review-v1"
CONTENT_MODE = "dacheng_buddhist"
_NORMAL_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
_SEMANTIC_SPLIT_RE = re.compile(r"(?<=[。！？；])|(?<=[，：、])")
_BUDDHIST_HINT_RE = re.compile(
    r"(佛|菩薩|如來|僧|禪|戒|定|慧|慈|悲|經|論|律|陀羅尼|三昧|涅槃|舍利|"
    r"毘|阿毘|修多羅|法身|功德|供養|無漏|阿羅漢|菩提)"
)
_HUMAN_SEMANTIC_RE = re.compile(r".+?[。！？；：，]|.+$", re.S)


def _srt_time(value: int) -> str:
    value = max(0, int(value))
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _segment_text(item: dict[str, Any]) -> str:
    return str(
        item.get("cleaned_text")
        or item.get("corrected_text")
        or item.get("text")
        or item.get("raw_text")
        or ""
    )


def _normalized_with_raw_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    raw_positions: list[int] = []
    for index, char in enumerate(str(value or "")):
        if _NORMAL_RE.fullmatch(char):
            normalized.append(char.lower())
            raw_positions.append(index)
    return "".join(normalized), raw_positions


def _source_stream(
    segments: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, int, int]]]:
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, item in enumerate(segments):
        normalized, _ = _normalized_with_raw_map(_segment_text(item))
        start = cursor
        parts.append(normalized)
        cursor += len(normalized)
        spans.append((start, cursor, index))
    return "".join(parts), spans


def _source_char_bounds(
    segments: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, int, int]]]:
    """Return normalized source text plus an approximate time box per char.

    Legacy fallback only.  This spreads one SRT cue's duration across its
    normalized characters and therefore must not be used when Chirp word
    timestamps are available.
    """

    chars: list[str] = []
    bounds: list[tuple[int, int, int]] = []
    for segment_index, item in enumerate(segments):
        normalized, _ = _normalized_with_raw_map(_segment_text(item))
        if not normalized:
            continue
        start = int(item.get("start_ms") or 0)
        end = int(item.get("end_ms") or start)
        duration = max(1, end - start)
        size = len(normalized)
        for index, char in enumerate(normalized):
            left = start + round(duration * index / size)
            right = start + round(duration * (index + 1) / size)
            if right <= left:
                right = left + 1
            chars.append(char)
            bounds.append((left, min(right, end), segment_index))
    return "".join(chars), bounds


def _word_char_bounds(
    merged_words: dict[str, Any] | list[dict[str, Any]],
) -> tuple[str, list[tuple[int, int, int]], list[dict[str, Any]]]:
    """Return normalized Chirp text with immutable word timing per character.

    Every normalized character originating from one Chirp word points to the
    *same* word start/end timestamps.  We intentionally do not synthesize
    sub-word timestamps.
    """

    raw_words = (
        merged_words.get("words", [])
        if isinstance(merged_words, dict)
        else merged_words
    )
    if not isinstance(raw_words, list):
        return "", [], []
    chars: list[str] = []
    bounds: list[tuple[int, int, int]] = []
    words: list[dict[str, Any]] = []
    previous_start = -1
    for raw in raw_words:
        if not isinstance(raw, dict):
            continue
        normalized, _ = _normalized_with_raw_map(str(raw.get("word") or ""))
        if not normalized:
            continue
        try:
            start = int(raw.get("start_ms"))
            end = int(raw.get("end_ms"))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start or start < previous_start:
            continue
        word_index = len(words)
        words.append(
            {
                "word": str(raw.get("word") or ""),
                "start_ms": start,
                "end_ms": end,
                "source_index": raw.get("source_index"),
            }
        )
        previous_start = start
        for char in normalized:
            chars.append(char)
            bounds.append((start, end, word_index))
    return "".join(chars), bounds, words


def word_timed_human_display_layer(
    source_segments: list[dict[str, Any]],
    human_text: str,
    omission_report: dict[str, Any],
    merged_words: dict[str, Any] | list[dict[str, Any]],
    *,
    minimum_matched_chars: int = 3,
    minimum_coverage: float = 0.25,
    max_unit_span_ms: int = 12_000,
    max_projection_source_chars: int = 160,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Map human-gold semantic units onto immutable Chirp word timestamps.

    Human transcript text is the spelling/semantic truth.  Chirp words are the
    timing truth.  The function never interpolates milliseconds inside a word:
    exact and context-projected mappings always snap to existing word
    start/end timestamps.
    """

    source_text, char_bounds, words = _word_char_bounds(merged_words)
    human_normalized, _ = _normalized_with_raw_map(human_text)
    units = _human_semantic_units(human_text)
    if not source_text or not char_bounds or not words or not human_normalized or not units:
        return deepcopy(source_segments), {
            "applied": False,
            "reason": "empty_or_invalid_merged_words_or_human",
            "timing_source": "legacy_srt_cue_proportional_fallback",
            "mapped_units": 0,
        }

    matcher = SequenceMatcher(None, source_text, human_normalized, autojunk=False)
    blocks = [
        block
        for block in matcher.get_matching_blocks()
        if int(block.size) > 0
    ]

    def bounded_context_projection(
        human_start: int,
        human_end: int,
    ) -> tuple[int, int] | None:
        left_block = None
        right_block = None
        for block in blocks:
            block_h_start = int(block.b)
            block_h_end = block_h_start + int(block.size)
            if block_h_end <= human_start:
                left_block = block
                continue
            if block_h_start >= human_end:
                right_block = block
                break
        if left_block is None or right_block is None:
            return None
        human_gap_start = int(left_block.b) + int(left_block.size)
        human_gap_end = int(right_block.b)
        source_gap_start = int(left_block.a) + int(left_block.size)
        source_gap_end = int(right_block.a)
        human_gap_size = human_gap_end - human_gap_start
        source_gap_size = source_gap_end - source_gap_start
        if (
            human_gap_size <= 0
            or source_gap_size <= 0
            or source_gap_size > max_projection_source_chars
            or human_start < human_gap_start
            or human_end > human_gap_end
        ):
            return None
        ratio = source_gap_size / human_gap_size
        if ratio < 0.4 or ratio > 2.5:
            return None
        projected_start = source_gap_start + round(
            source_gap_size * (human_start - human_gap_start) / human_gap_size
        )
        projected_end = source_gap_start + round(
            source_gap_size * (human_end - human_gap_start) / human_gap_size
        )
        projected_start = max(source_gap_start, min(projected_start, source_gap_end - 1))
        projected_end = max(projected_start + 1, min(projected_end, source_gap_end))
        return projected_start, projected_end

    output: list[dict[str, Any]] = []
    unmapped_units: list[dict[str, Any]] = []
    mapped_chars = 0
    context_projected_units = 0
    context_projected_run_units = 0
    exact_units = 0
    rejected_implausible_units = 0
    mapped_by_unit: dict[int, dict[str, Any]] = {}
    for unit_index, unit in enumerate(units):
        human_start = int(unit["human_start"])
        human_end = int(unit["human_end"])
        matched_ranges: list[tuple[int, int, int, int]] = []
        matched = 0
        for block in blocks:
            block_h_start = int(block.b)
            block_h_end = block_h_start + int(block.size)
            left = max(human_start, block_h_start)
            right = min(human_end, block_h_end)
            if right <= left:
                continue
            width = right - left
            source_start = int(block.a) + (left - block_h_start)
            matched_ranges.append((source_start, source_start + width, left, right))
            matched += width
        coverage = matched / max(1, int(unit["normalized_chars"]))
        exact_supported = bool(matched_ranges) and (
            (matched >= minimum_matched_chars and coverage >= minimum_coverage)
            or coverage >= 0.5
        )
        mapping_method = "exact_word_anchor"
        source_range: tuple[int, int] | None = None
        if exact_supported:
            source_start = min(value[0] for value in matched_ranges)
            source_end = max(value[1] for value in matched_ranges)
            first = min(matched_ranges, key=lambda value: value[2])
            last = max(matched_ranges, key=lambda value: value[3])
            # A typo at the first/last character should still retain the
            # timing of the corresponding Chirp word.  Extend by unsupported
            # edge character counts, but only in source character space.
            missing_prefix = max(0, first[2] - human_start)
            missing_suffix = max(0, human_end - last[3])
            source_start = max(0, source_start - missing_prefix)
            source_end = min(len(char_bounds), source_end + missing_suffix)
            if source_end > source_start:
                source_range = (source_start, source_end)
                exact_units += 1
        else:
            source_range = bounded_context_projection(human_start, human_end)
            if source_range is not None:
                mapping_method = "context_projected_word_boundary"
                context_projected_units += 1

        if source_range is None:
            unmapped_units.append(
                {
                    "unit_index": unit_index,
                    "text": str(unit["text"]),
                    "human_start": human_start,
                    "human_end": human_end,
                    "matched_chars": matched,
                    "coverage": round(coverage, 4),
                }
            )
            continue

        source_start, source_end = source_range
        source_start = max(0, source_start)
        source_end = min(len(char_bounds), source_end)
        if source_end <= source_start:
            continue
        first_word = int(char_bounds[source_start][2])
        last_word = int(char_bounds[source_end - 1][2])
        if last_word < first_word:
            continue
        start_ms = int(words[first_word]["start_ms"])
        end_ms = int(words[last_word]["end_ms"])
        unit_chars = max(1, int(unit["normalized_chars"]))
        allowed_span_ms = min(
            30_000,
            max(max_unit_span_ms, unit_chars * 1_200),
        )
        if end_ms <= start_ms or end_ms - start_ms > allowed_span_ms:
            rejected_implausible_units += 1
            unmapped_units.append(
                {
                    "unit_index": unit_index,
                    "text": str(unit["text"]),
                    "human_start": human_start,
                    "human_end": human_end,
                    "matched_chars": matched,
                    "coverage": round(coverage, 4),
                    "reason": "implausible_word_timing_span",
                    "timing_span_ms": max(0, end_ms - start_ms),
                    "allowed_timing_span_ms": allowed_span_ms,
                }
            )
            continue
        source_ids = [
            str(item.get("segment_id") or "")
            for item in source_segments
            if int(item.get("end_ms") or 0) > start_ms
            and int(item.get("start_ms") or 0) < end_ms
        ]
        mapped_item = {
                "segment_id": f"human-word-display-{unit_index + 1:04d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "raw_text": "",
                "text": str(unit["text"]),
                "corrected_text": str(unit["text"]),
                "cleaned_text": str(unit["text"]),
                "source_segment_ids": [value for value in source_ids if value],
                "source_word_start_index": first_word,
                "source_word_end_index": last_word,
                "source_char_start": source_start,
                "source_char_end": source_end,
                "human_unit_index": unit_index,
                "cleanup_actions": ["human_word_timed_semantic_alignment"],
                "cleanup_review_reasons": [],
                "human_alignment_coverage": round(coverage, 4),
                "human_mapping_method": mapping_method,
                "timing_source": "chirp_word_timestamps",
            }
        output.append(mapped_item)
        mapped_by_unit[unit_index] = mapped_item
        mapped_chars += matched

    if unmapped_units and mapped_by_unit:
        unresolved_indexes = {
            int(item["unit_index"])
            for item in unmapped_units
            if item.get("unit_index") is not None
        }
        projected_indexes: set[int] = set()
        cursor = 0
        while cursor < len(units):
            if cursor not in unresolved_indexes:
                cursor += 1
                continue
            run_start = cursor
            while cursor + 1 < len(units) and cursor + 1 in unresolved_indexes:
                cursor += 1
            run_end = cursor
            left = mapped_by_unit.get(run_start - 1)
            right = mapped_by_unit.get(run_end + 1)
            if left is None or right is None:
                cursor += 1
                continue
            source_gap_start = int(left.get("source_char_end") or 0)
            source_gap_end = int(right.get("source_char_start") or 0)
            source_gap_chars = source_gap_end - source_gap_start
            human_gap_chars = sum(
                max(1, int(units[index]["normalized_chars"]))
                for index in range(run_start, run_end + 1)
            )
            if (
                source_gap_chars <= 0
                or human_gap_chars <= 0
                or source_gap_end > len(char_bounds)
            ):
                cursor += 1
                continue
            ratio = source_gap_chars / human_gap_chars
            first_gap_word = int(char_bounds[source_gap_start][2])
            last_gap_word = int(char_bounds[source_gap_end - 1][2])
            if last_gap_word < first_gap_word:
                cursor += 1
                continue
            gap_start_ms = int(words[first_gap_word]["start_ms"])
            gap_end_ms = int(words[last_gap_word]["end_ms"])
            gap_duration_ms = gap_end_ms - gap_start_ms
            if (
                source_gap_chars > 2_500
                or ratio < 0.35
                or ratio > 3.0
                or gap_duration_ms <= 0
                or gap_duration_ms > 600_000
                or gap_duration_ms / human_gap_chars > 1_800
            ):
                cursor += 1
                continue

            accumulated = 0
            previous_word = int(left.get("source_word_end_index") or -1)
            projected_run: list[dict[str, Any]] = []
            run_failed = False
            for unit_index in range(run_start, run_end + 1):
                unit = units[unit_index]
                width = max(1, int(unit["normalized_chars"]))
                source_start = source_gap_start + round(
                    source_gap_chars * accumulated / human_gap_chars
                )
                accumulated += width
                source_end = source_gap_start + round(
                    source_gap_chars * accumulated / human_gap_chars
                )
                source_start = max(
                    source_gap_start,
                    min(source_start, source_gap_end - 1),
                )
                source_end = max(
                    source_start + 1,
                    min(source_end, source_gap_end),
                )
                first_word = max(
                    previous_word + 1,
                    int(char_bounds[source_start][2]),
                )
                last_word = int(char_bounds[source_end - 1][2])
                right_start_word = int(
                    right.get("source_word_start_index") or len(words)
                )
                if (
                    first_word > last_word
                    or last_word >= right_start_word
                    or last_word >= len(words)
                ):
                    run_failed = True
                    break
                start_ms = int(words[first_word]["start_ms"])
                end_ms = int(words[last_word]["end_ms"])
                allowed_span_ms = min(
                    30_000,
                    max(
                        max_unit_span_ms,
                        max(1, int(unit["normalized_chars"])) * 1_200,
                    ),
                )
                if end_ms <= start_ms or end_ms - start_ms > allowed_span_ms:
                    run_failed = True
                    break
                source_ids = [
                    str(item.get("segment_id") or "")
                    for item in source_segments
                    if int(item.get("end_ms") or 0) > start_ms
                    and int(item.get("start_ms") or 0) < end_ms
                ]
                projected_run.append(
                    {
                        "segment_id": f"human-word-display-{unit_index + 1:04d}",
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "raw_text": "",
                        "text": str(unit["text"]),
                        "corrected_text": str(unit["text"]),
                        "cleaned_text": str(unit["text"]),
                        "source_segment_ids": [
                            value for value in source_ids if value
                        ],
                        "source_word_start_index": first_word,
                        "source_word_end_index": last_word,
                        "source_char_start": source_start,
                        "source_char_end": source_end,
                        "human_unit_index": unit_index,
                        "cleanup_actions": ["human_word_timed_run_projection"],
                        "cleanup_review_reasons": [],
                        "human_alignment_coverage": 0.0,
                        "human_mapping_method": "context_projected_word_run",
                        "timing_source": "chirp_word_timestamps",
                    }
                )
                previous_word = last_word
            if not run_failed and len(projected_run) == run_end - run_start + 1:
                for mapped_item in projected_run:
                    unit_index = int(mapped_item["human_unit_index"])
                    output.append(mapped_item)
                    mapped_by_unit[unit_index] = mapped_item
                    projected_indexes.add(unit_index)
                    context_projected_run_units += 1
            cursor += 1

        if projected_indexes:
            unmapped_units = [
                item
                for item in unmapped_units
                if int(item.get("unit_index") or -1) not in projected_indexes
            ]

    preserved_source_ids: set[str] = set()
    skipped_already_mapped_source_ids: set[str] = set()
    mapped_source_ids = {
        str(source_id)
        for item in output
        if str(item.get("segment_id") or "").startswith("human-word-display-")
        for source_id in item.get("source_segment_ids", [])
        if str(source_id)
    }
    for candidate in omission_report.get("candidates", []):
        if candidate.get("direction") != "human_missing":
            continue
        left = int(candidate.get("start_ms") or 0)
        right = int(candidate.get("end_ms") or left)
        for source in source_segments:
            start = int(source.get("start_ms") or 0)
            end = int(source.get("end_ms") or start)
            if end <= left or start >= right:
                continue
            source_id = str(source.get("segment_id") or "")
            if not source_id or source_id in preserved_source_ids:
                continue
            if source_id in mapped_source_ids:
                skipped_already_mapped_source_ids.add(source_id)
                continue
            preserved = deepcopy(source)
            preserved["corrected_text"] = _segment_text(source)
            preserved["cleaned_text"] = _segment_text(source)
            preserved["timing_source"] = "source_srt_preserved_human_missing"
            preserved.setdefault("source_segment_ids", [source_id])
            preserved.setdefault("cleanup_actions", []).append(
                "preserved_human_missing_source"
            )
            output.append(preserved)
            preserved_source_ids.add(source_id)

    output.sort(
        key=lambda item: (
            int(item.get("start_ms") or 0),
            int(item.get("end_ms") or 0),
            str(item.get("segment_id") or ""),
        )
    )
    monotonic: list[dict[str, Any]] = []
    overlap_merges = 0
    for item in output:
        if not monotonic:
            monotonic.append(item)
            continue
        previous = monotonic[-1]
        start = int(item.get("start_ms") or 0)
        previous_end = int(previous.get("end_ms") or 0)
        if start < previous_end:
            previous["cleaned_text"] = (
                _segment_text(previous).rstrip() + _segment_text(item).lstrip()
            )
            previous["corrected_text"] = previous["cleaned_text"]
            previous["end_ms"] = max(
                previous_end,
                int(item.get("end_ms") or previous_end),
            )
            previous.setdefault("source_segment_ids", []).extend(
                value
                for value in item.get("source_segment_ids", [])
                if value not in previous.get("source_segment_ids", [])
            )
            previous.setdefault("cleanup_actions", []).append(
                "merged_word_timing_overlap"
            )
            overlap_merges += 1
            continue
        monotonic.append(item)

    return monotonic, {
        "applied": bool(monotonic),
        "timing_source": "chirp_word_timestamps",
        "legacy_fallback": False,
        "mapped_units": sum(
            str(item.get("segment_id") or "").startswith("human-word-display-")
            for item in monotonic
        ),
        "semantic_unit_count": len(units),
        "unmapped_unit_count": len(unmapped_units),
        "unmapped_units": unmapped_units,
        "mapped_char_evidence": mapped_chars,
        "human_normalized_chars": len(human_normalized),
        "exact_word_anchor_units": exact_units,
        "context_projected_units": context_projected_units,
        "context_projected_run_units": context_projected_run_units,
        "rejected_implausible_units": rejected_implausible_units,
        "preserved_human_missing_source_cues": len(preserved_source_ids),
        "skipped_human_missing_already_mapped_source_cues": len(
            skipped_already_mapped_source_ids
        ),
        "overlap_merges": overlap_merges,
        "word_count": len(words),
    }


def _human_semantic_units(value: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    cursor = 0
    paragraph_index = 0
    for paragraph in re.split(r"\n+", str(value or "")):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        current_paragraph_index = paragraph_index
        paragraph_index += 1
        for match in _HUMAN_SEMANTIC_RE.finditer(paragraph):
            text = match.group(0).strip()
            normalized, _ = _normalized_with_raw_map(text)
            if not normalized:
                continue
            start = cursor
            cursor += len(normalized)
            units.append(
                {
                    "text": text,
                    "human_start": start,
                    "human_end": cursor,
                    "normalized_chars": len(normalized),
                    "paragraph_index": current_paragraph_index,
                }
            )
    return units


def sequential_word_timed_human_display_layer(
    source_segments: list[dict[str, Any]],
    human_text: str,
    omission_report: dict[str, Any],
    merged_words: dict[str, Any] | list[dict[str, Any]],
    *,
    minimum_anchor_chars: int = 3,
    minimum_paragraph_coverage: float = 0.14,
    max_search_source_chars: int = 8_000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Align human paragraphs forward-only onto Chirp word timing.

    Paragraph order is authoritative: once a paragraph consumes a source word
    range, later paragraphs may only search after that range. This prevents a
    repeated Buddhist phrase later in a lesson from being matched to an
    earlier acoustic occurrence.
    """

    source_text, char_bounds, words = _word_char_bounds(merged_words)
    units = _human_semantic_units(human_text)
    if not source_text or not char_bounds or not words or not units:
        return deepcopy(source_segments), {
            "applied": False,
            "reason": "empty_or_invalid_merged_words_or_human",
            "timing_source": "legacy_srt_cue_proportional_fallback",
            "mapped_units": 0,
        }

    paragraphs: list[list[tuple[int, dict[str, Any]]]] = []
    current_index: int | None = None
    current: list[tuple[int, dict[str, Any]]] = []
    for unit_index, unit in enumerate(units):
        paragraph_index = int(unit.get("paragraph_index") or 0)
        if current_index is None or paragraph_index == current_index:
            current.append((unit_index, unit))
            current_index = paragraph_index
            continue
        paragraphs.append(current)
        current = [(unit_index, unit)]
        current_index = paragraph_index
    if current:
        paragraphs.append(current)

    output: list[dict[str, Any]] = []
    unmapped_units: list[dict[str, Any]] = []
    paragraph_reports: list[dict[str, Any]] = []
    source_cursor = 0
    previous_word = -1
    mapped_paragraphs = 0
    projected_units = 0
    exact_units = 0

    for paragraph_order, paragraph_units in enumerate(paragraphs):
        paragraph_text = "".join(
            _normalized_with_raw_map(str(unit["text"]))[0]
            for _, unit in paragraph_units
        )
        if not paragraph_text:
            continue
        search_size = min(
            max_search_source_chars,
            max(1_500, len(paragraph_text) * 12 + 800),
        )
        window_start = source_cursor
        window_end = min(len(source_text), window_start + search_size)
        if window_end <= window_start:
            for unit_index, unit in paragraph_units:
                unmapped_units.append(
                    {
                        "unit_index": unit_index,
                        "text": str(unit["text"]),
                        "reason": "source_exhausted",
                    }
                )
            continue
        window = source_text[window_start:window_end]
        matcher = SequenceMatcher(None, window, paragraph_text, autojunk=False)
        raw_blocks = [
            block
            for block in matcher.get_matching_blocks()
            if int(block.size) >= minimum_anchor_chars
        ]
        clusters: list[list[Any]] = []
        for block in raw_blocks:
            if not clusters:
                clusters.append([block])
                continue
            previous = clusters[-1][-1]
            source_gap = int(block.a) - (int(previous.a) + int(previous.size))
            target_gap = int(block.b) - (int(previous.b) + int(previous.size))
            split_threshold = max(250, len(paragraph_text) * 2)
            if (
                source_gap > split_threshold
                and source_gap > max(80, target_gap * 4 + 40)
            ):
                clusters.append([block])
            else:
                clusters[-1].append(block)
        blocks = max(
            clusters,
            key=lambda cluster: (
                sum(int(block.size) for block in cluster),
                -int(cluster[0].a),
            ),
            default=[],
        )
        matched = sum(int(block.size) for block in blocks)
        coverage = matched / max(1, len(paragraph_text))
        if not blocks or (
            coverage < minimum_paragraph_coverage
            and matched < max(10, min(30, len(paragraph_text) // 3))
        ):
            paragraph_reports.append(
                {
                    "paragraph_order": paragraph_order,
                    "mapped": False,
                    "matched_chars": matched,
                    "coverage": round(coverage, 4),
                    "source_cursor": source_cursor,
                }
            )
            for unit_index, unit in paragraph_units:
                unmapped_units.append(
                    {
                        "unit_index": unit_index,
                        "text": str(unit["text"]),
                        "matched_chars": matched,
                        "coverage": round(coverage, 4),
                        "reason": "paragraph_anchor_insufficient",
                    }
                )
            continue

        first_block = min(blocks, key=lambda block: int(block.b))
        last_block = max(blocks, key=lambda block: int(block.b) + int(block.size))
        target_anchor_start = int(first_block.b)
        target_anchor_end = int(last_block.b) + int(last_block.size)
        source_anchor_start = window_start + int(first_block.a)
        source_anchor_end = window_start + int(last_block.a) + int(last_block.size)
        missing_prefix = target_anchor_start
        missing_suffix = max(0, len(paragraph_text) - target_anchor_end)
        paragraph_source_start = max(
            source_cursor,
            source_anchor_start - missing_prefix,
        )
        paragraph_source_end = min(
            len(source_text),
            source_anchor_end + missing_suffix,
        )
        source_span_chars = paragraph_source_end - paragraph_source_start
        if source_span_chars <= 0:
            for unit_index, unit in paragraph_units:
                unmapped_units.append(
                    {
                        "unit_index": unit_index,
                        "text": str(unit["text"]),
                        "reason": "paragraph_source_span_invalid",
                    }
                )
            continue
        span_ratio = source_span_chars / max(1, len(paragraph_text))
        first_word = int(char_bounds[paragraph_source_start][2])
        last_word = int(char_bounds[paragraph_source_end - 1][2])
        start_ms = int(words[first_word]["start_ms"])
        end_ms = int(words[last_word]["end_ms"])
        duration_ms = end_ms - start_ms
        if (
            first_word <= previous_word
            or last_word < first_word
            or span_ratio < 0.30
            or span_ratio > 3.2
            or duration_ms <= 0
            or duration_ms > 600_000
            or duration_ms / max(1, len(paragraph_text)) > 2_000
        ):
            paragraph_reports.append(
                {
                    "paragraph_order": paragraph_order,
                    "mapped": False,
                    "matched_chars": matched,
                    "coverage": round(coverage, 4),
                    "reason": "paragraph_span_implausible",
                    "source_span_chars": source_span_chars,
                    "duration_ms": max(0, duration_ms),
                }
            )
            for unit_index, unit in paragraph_units:
                unmapped_units.append(
                    {
                        "unit_index": unit_index,
                        "text": str(unit["text"]),
                        "matched_chars": matched,
                        "coverage": round(coverage, 4),
                        "reason": "paragraph_span_implausible",
                    }
                )
            continue

        local_blocks = [
            type(block)(
                int(block.a) + window_start - paragraph_source_start,
                int(block.b),
                int(block.size),
            )
            for block in blocks
            if window_start + int(block.a) >= paragraph_source_start
            and window_start + int(block.a) + int(block.size) <= paragraph_source_end
        ]
        paragraph_cursor = 0
        paragraph_items: list[dict[str, Any]] = []
        paragraph_previous_word = previous_word
        paragraph_failed = False
        paragraph_failure_reason = "paragraph_unit_projection_failed"
        same_word_merge_count = 0
        for unit_index, unit in paragraph_units:
            width = max(1, int(unit["normalized_chars"]))
            unit_target_start = paragraph_cursor
            unit_target_end = min(len(paragraph_text), paragraph_cursor + width)
            paragraph_cursor = unit_target_end
            matched_ranges: list[tuple[int, int, int, int]] = []
            unit_matched = 0
            for block in local_blocks:
                block_target_start = int(block.b)
                block_target_end = block_target_start + int(block.size)
                left = max(unit_target_start, block_target_start)
                right = min(unit_target_end, block_target_end)
                if right <= left:
                    continue
                size = right - left
                source_start = int(block.a) + (left - block_target_start)
                matched_ranges.append(
                    (source_start, source_start + size, left, right)
                )
                unit_matched += size
            unit_coverage = unit_matched / max(1, width)
            if matched_ranges and (
                unit_matched >= minimum_anchor_chars or unit_coverage >= 0.5
            ):
                first = min(matched_ranges, key=lambda value: value[2])
                last = max(matched_ranges, key=lambda value: value[3])
                source_start = min(value[0] for value in matched_ranges)
                source_end = max(value[1] for value in matched_ranges)
                source_start = max(
                    0,
                    source_start - max(0, first[2] - unit_target_start),
                )
                source_end = min(
                    source_span_chars,
                    source_end + max(0, unit_target_end - last[3]),
                )
                method = "sequential_paragraph_exact_word_anchor"
                exact_units += 1
            else:
                source_start = round(
                    source_span_chars * unit_target_start / max(1, len(paragraph_text))
                )
                source_end = round(
                    source_span_chars * unit_target_end / max(1, len(paragraph_text))
                )
                source_start = max(0, min(source_start, source_span_chars - 1))
                source_end = max(
                    source_start + 1,
                    min(source_end, source_span_chars),
                )
                method = "sequential_paragraph_word_projection"
                projected_units += 1
            absolute_start = paragraph_source_start + source_start
            absolute_end = paragraph_source_start + source_end
            unit_first_word = max(
                paragraph_previous_word + 1,
                int(char_bounds[absolute_start][2]),
            )
            unit_last_word = int(char_bounds[absolute_end - 1][2])
            if unit_first_word > unit_last_word:
                if (
                    paragraph_items
                    and unit_last_word <= paragraph_previous_word
                ):
                    paragraph_items[-1]["cleaned_text"] = (
                        _segment_text(paragraph_items[-1]).rstrip()
                        + str(unit["text"]).lstrip()
                    )
                    paragraph_items[-1]["corrected_text"] = paragraph_items[-1][
                        "cleaned_text"
                    ]
                    paragraph_items[-1]["source_char_end"] = max(
                        int(paragraph_items[-1].get("source_char_end") or 0),
                        absolute_end,
                    )
                    paragraph_items[-1].setdefault("cleanup_actions", []).append(
                        "merged_same_chirp_word_semantic_unit"
                    )
                    same_word_merge_count += 1
                    collapsed_text, _ = _normalized_with_raw_map(
                        _segment_text(paragraph_items[-1])
                    )
                    collapsed_duration = max(
                        1,
                        int(paragraph_items[-1]["end_ms"])
                        - int(paragraph_items[-1]["start_ms"]),
                    )
                    collapsed_cps = len(collapsed_text) * 1000.0 / collapsed_duration
                    if (
                        same_word_merge_count >= 2
                        and len(collapsed_text) >= 16
                        and collapsed_cps > 10.0
                    ):
                        paragraph_failed = True
                        paragraph_failure_reason = (
                            "same_chirp_word_semantic_collapse_density"
                        )
                        break
                    continue
                paragraph_failed = True
                break
            if unit_last_word > last_word:
                paragraph_failed = True
                break
            unit_start_ms = int(words[unit_first_word]["start_ms"])
            unit_end_ms = int(words[unit_last_word]["end_ms"])
            allowed_span_ms = min(
                30_000,
                max(12_000, width * 1_200),
            )
            if (
                unit_end_ms <= unit_start_ms
                or unit_end_ms - unit_start_ms > allowed_span_ms
            ):
                paragraph_failed = True
                break
            source_ids = [
                str(item.get("segment_id") or "")
                for item in source_segments
                if int(item.get("end_ms") or 0) > unit_start_ms
                and int(item.get("start_ms") or 0) < unit_end_ms
            ]
            paragraph_items.append(
                {
                    "segment_id": f"human-sequential-{unit_index + 1:04d}",
                    "start_ms": unit_start_ms,
                    "end_ms": unit_end_ms,
                    "raw_text": "",
                    "text": str(unit["text"]),
                    "corrected_text": str(unit["text"]),
                    "cleaned_text": str(unit["text"]),
                    "source_segment_ids": [value for value in source_ids if value],
                    "source_word_start_index": unit_first_word,
                    "source_word_end_index": unit_last_word,
                    "source_char_start": absolute_start,
                    "source_char_end": absolute_end,
                    "human_unit_index": unit_index,
                    "cleanup_actions": ["sequential_paragraph_word_alignment"],
                    "cleanup_review_reasons": [],
                    "human_alignment_coverage": round(unit_coverage, 4),
                    "human_mapping_method": method,
                    "timing_source": "chirp_word_timestamps",
                }
            )
            paragraph_previous_word = unit_last_word

        if paragraph_failed or not paragraph_items:
            if (
                paragraph_failure_reason
                == "same_chirp_word_semantic_collapse_density"
            ):
                fallback_items = _chirp_word_semantic_fallback(
                    words,
                    first_word,
                    last_word,
                    segment_prefix=f"chirp-fallback-p{paragraph_order:04d}",
                )
                if fallback_items:
                    output.extend(fallback_items)
                    mapped_paragraphs += 1
                    previous_word = last_word
                    source_cursor = paragraph_source_end
                    while (
                        source_cursor < len(char_bounds)
                        and int(char_bounds[source_cursor][2]) <= previous_word
                    ):
                        source_cursor += 1
                    paragraph_reports.append(
                        {
                            "paragraph_order": paragraph_order,
                            "mapped": True,
                            "matched_chars": matched,
                            "coverage": round(coverage, 4),
                            "reason": paragraph_failure_reason,
                            "fail_closed_to_chirp_words": True,
                            "fallback_cue_count": len(fallback_items),
                            "source_word_start_index": first_word,
                            "source_word_end_index": last_word,
                        }
                    )
                    for unit_index, unit in paragraph_units:
                        unmapped_units.append(
                            {
                                "unit_index": unit_index,
                                "text": str(unit["text"]),
                                "matched_chars": matched,
                                "coverage": round(coverage, 4),
                                "reason": paragraph_failure_reason,
                                "reference_only_not_forced_into_audio": True,
                            }
                        )
                    continue
            paragraph_reports.append(
                {
                    "paragraph_order": paragraph_order,
                    "mapped": False,
                    "matched_chars": matched,
                    "coverage": round(coverage, 4),
                    "reason": paragraph_failure_reason,
                }
            )
            for unit_index, unit in paragraph_units:
                unmapped_units.append(
                    {
                        "unit_index": unit_index,
                        "text": str(unit["text"]),
                        "matched_chars": matched,
                        "coverage": round(coverage, 4),
                        "reason": paragraph_failure_reason,
                    }
                )
            continue

        output.extend(paragraph_items)
        mapped_paragraphs += 1
        previous_word = int(paragraph_items[-1]["source_word_end_index"])
        source_cursor = int(paragraph_items[-1]["source_char_end"])
        # One Chirp word can normalize to multiple characters.  A semantic
        # paragraph may therefore end in the middle of that word's character
        # span.  Never let the next paragraph reuse the same acoustic word:
        # advance the source cursor through every remaining character that
        # still belongs to the consumed word.  This keeps alignment monotonic
        # in word space rather than merely character space.
        while (
            source_cursor < len(char_bounds)
            and int(char_bounds[source_cursor][2]) <= previous_word
        ):
            source_cursor += 1
        paragraph_reports.append(
            {
                "paragraph_order": paragraph_order,
                "mapped": True,
                "matched_chars": matched,
                "coverage": round(coverage, 4),
                "source_word_start_index": int(
                    paragraph_items[0]["source_word_start_index"]
                ),
                "source_word_end_index": previous_word,
            }
        )

    mapped_source_ids = {
        str(source_id)
        for item in output
        for source_id in item.get("source_segment_ids", [])
        if str(source_id)
    }
    preserved_source_ids: set[str] = set()
    for candidate in omission_report.get("candidates", []):
        if candidate.get("direction") != "human_missing":
            continue
        left = int(candidate.get("start_ms") or 0)
        right = int(candidate.get("end_ms") or left)
        for source in source_segments:
            start = int(source.get("start_ms") or 0)
            end = int(source.get("end_ms") or start)
            if end <= left or start >= right:
                continue
            source_id = str(source.get("segment_id") or "")
            if (
                not source_id
                or source_id in mapped_source_ids
                or source_id in preserved_source_ids
            ):
                continue
            preserved = deepcopy(source)
            preserved["corrected_text"] = _segment_text(source)
            preserved["cleaned_text"] = _segment_text(source)
            preserved["timing_source"] = "source_srt_preserved_human_missing"
            preserved.setdefault("source_segment_ids", [source_id])
            preserved.setdefault("cleanup_actions", []).append(
                "preserved_human_missing_source"
            )
            output.append(preserved)
            preserved_source_ids.add(source_id)

    output.sort(
        key=lambda item: (
            int(item.get("start_ms") or 0),
            int(item.get("end_ms") or 0),
        )
    )
    monotonic: list[dict[str, Any]] = []
    overlap_merges = 0
    for item in output:
        if not monotonic:
            monotonic.append(item)
            continue
        previous = monotonic[-1]
        if int(item.get("start_ms") or 0) < int(previous.get("end_ms") or 0):
            previous["cleaned_text"] = (
                _segment_text(previous).rstrip() + _segment_text(item).lstrip()
            )
            previous["corrected_text"] = previous["cleaned_text"]
            previous["end_ms"] = max(
                int(previous.get("end_ms") or 0),
                int(item.get("end_ms") or 0),
            )
            overlap_merges += 1
            continue
        monotonic.append(item)

    return monotonic, {
        "applied": bool(monotonic),
        "timing_source": "chirp_word_timestamps",
        "alignment_strategy": "sequential_forward_paragraphs",
        "legacy_fallback": False,
        "mapped_units": sum(
            str(item.get("segment_id") or "").startswith("human-sequential-")
            for item in monotonic
        ),
        "semantic_unit_count": len(units),
        "paragraph_count": len(paragraphs),
        "mapped_paragraph_count": mapped_paragraphs,
        "unmapped_unit_count": len(unmapped_units),
        "unmapped_units": unmapped_units,
        "exact_word_anchor_units": exact_units,
        "projected_word_units": projected_units,
        "preserved_human_missing_source_cues": len(preserved_source_ids),
        "overlap_merges": overlap_merges,
        "word_count": len(words),
        "paragraph_reports": paragraph_reports,
    }


def word_timed_scripture_verification_layer(
    display: list[dict[str, Any]],
    canonical: dict[str, Any] | None,
    *,
    minimum_contiguous_match_chars: int = 80,
    scan_end_ms: int = 600_000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify canonical scripture without changing word-derived cue timing."""

    if canonical is None or not str(canonical.get("body_text") or "").strip():
        return display, {
            "applied": False,
            "reason": "canonical_scripture_missing",
            "review_required": True,
        }
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, item in enumerate(display):
        if int(item.get("start_ms") or 0) > scan_end_ms:
            continue
        key = text_key(_segment_text(item))
        if not key:
            continue
        start = cursor
        parts.append(key)
        cursor += len(key)
        spans.append((start, cursor, index))
    source = "".join(parts)
    target = text_key(str(canonical.get("body_text") or ""))
    if not source or not target:
        return display, {
            "applied": False,
            "reason": "scripture_word_timed_verification_review",
            "review_required": True,
            "detail": "empty_verification_stream",
        }
    matcher = SequenceMatcher(None, source, target, autojunk=False)
    blocks = [
        block
        for block in matcher.get_matching_blocks()
        if int(block.size) >= minimum_contiguous_match_chars
    ]
    if not blocks:
        return display, {
            "applied": False,
            "reason": "scripture_word_timed_verification_review",
            "review_required": True,
            "canonical_version": canonical.get("active_version"),
            "canonical_checksum": canonical.get("active_checksum"),
            "detail": "no_contiguous_canonical_passage",
        }
    best = max(blocks, key=lambda block: int(block.size))
    source_start = int(best.a)
    source_end = source_start + int(best.size)
    selected_indexes = [
        segment_index
        for span_start, span_end, segment_index in spans
        if span_end > source_start and span_start < source_end
    ]
    if not selected_indexes:
        return display, {
            "applied": False,
            "reason": "scripture_word_timed_verification_review",
            "review_required": True,
            "detail": "canonical_passage_has_no_display_segments",
        }
    first = display[min(selected_indexes)]
    last = display[max(selected_indexes)]
    return display, {
        "applied": True,
        "match": "contiguous_canonical_passage_in_word_timed_human_text",
        "review_required": False,
        "canonical_version": canonical.get("active_version"),
        "canonical_checksum": canonical.get("active_checksum"),
        "matched_chars": int(best.size),
        "source_start_segment_id": first.get("segment_id"),
        "source_end_segment_id": last.get("segment_id"),
        "source_start_ms": int(first.get("start_ms") or 0),
        "source_end_ms": int(last.get("end_ms") or 0),
        "timing_source": "chirp_word_timestamps",
        "text_source": "human_transcript_verified_against_canonical",
    }


def word_timed_mantra_region_layer(
    display: list[dict[str, Any]],
    human_text: str,
    canonical: dict[str, Any] | None,
    merged_words: dict[str, Any] | list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Render canonical mantra cycles on existing Chirp word boundaries only."""

    if canonical is None or not str(canonical.get("body_text") or "").strip():
        return display, {
            "applied": False,
            "reason": "canonical_mantra_missing",
            "review_required": True,
        }
    lines = canonical_lines(str(canonical.get("body_text") or ""))
    canonical_normalized, _ = _normalized_with_raw_map(
        str(canonical.get("body_text") or "")
    )
    human_normalized, _ = _normalized_with_raw_map(human_text)
    cycle_count = (
        human_normalized.count(canonical_normalized)
        if canonical_normalized
        else 0
    )
    if not lines or cycle_count <= 0:
        return display, {
            "applied": False,
            "reason": "canonical_mantra_cycles_not_present_in_human_transcript",
            "review_required": True,
            "canonical_version": canonical.get("active_version"),
            "canonical_checksum": canonical.get("active_checksum"),
        }

    title_index = next(
        (
            index
            for index in range(len(display) - 1, -1, -1)
            if re.search(
                r"彌勒.*根本.*大明神咒|根本大明神咒",
                _segment_text(display[index]),
            )
        ),
        None,
    )
    if title_index is None:
        return display, {
            "applied": False,
            "reason": "word_timed_mantra_title_not_found",
            "review_required": True,
        }
    closing_index = next(
        (
            index
            for index in range(title_index + 1, len(display))
            if re.search(
                r"大眾.*起立|向佛.*問訊|法會圓滿",
                _segment_text(display[index]),
            )
        ),
        None,
    )
    if closing_index is None:
        return display, {
            "applied": False,
            "reason": "word_timed_mantra_closing_not_found",
            "review_required": True,
        }

    _, _, words = _word_char_bounds(merged_words)
    if not words:
        return display, {
            "applied": False,
            "reason": "word_timed_mantra_words_missing",
            "review_required": True,
        }
    title = display[title_index]
    closing = display[closing_index]
    first_word = int(title.get("source_word_end_index") or -1) + 1
    last_word = int(closing.get("source_word_start_index") or len(words)) - 1
    if first_word < 0 or first_word >= len(words):
        title_end_ms = int(title.get("end_ms") or 0)
        first_word = next(
            (
                index
                for index, word in enumerate(words)
                if int(word["start_ms"]) >= title_end_ms
            ),
            -1,
        )
    if last_word < first_word or last_word >= len(words):
        closing_start_ms = int(closing.get("start_ms") or 0)
        candidates = [
            index
            for index, word in enumerate(words)
            if int(word["end_ms"]) <= closing_start_ms
        ]
        last_word = candidates[-1] if candidates else -1

    sequence = lines * cycle_count
    total_words = last_word - first_word + 1
    if first_word < 0 or last_word < first_word or total_words < len(sequence):
        return display, {
            "applied": False,
            "reason": "word_timed_mantra_region_invalid",
            "review_required": True,
            "source_word_count": max(0, total_words),
            "required_line_count": len(sequence),
        }

    weights = [
        max(1, len(_normalized_with_raw_map(line)[0]))
        for line in sequence
    ]
    total_weight = sum(weights)
    boundaries = [first_word]
    accumulated = 0
    for unit_index, weight in enumerate(weights[:-1]):
        accumulated += weight
        remaining_units = len(sequence) - unit_index - 1
        target = first_word + round(total_words * accumulated / total_weight)
        minimum = boundaries[-1] + 1
        maximum = last_word - remaining_units + 1
        target = max(minimum, min(target, maximum))
        radius = max(4, min(16, total_words // max(1, len(sequence) * 2)))
        candidate_indexes = range(
            max(minimum, target - radius),
            min(maximum, target + radius) + 1,
        )
        boundary = min(
            candidate_indexes,
            key=lambda index: (
                abs(index - target) * 120
                - min(
                    2_000,
                    max(
                        0,
                        int(words[index]["start_ms"])
                        - int(words[index - 1]["end_ms"]),
                    ),
                )
            ),
        )
        boundaries.append(boundary)
    boundaries.append(last_word + 1)

    cues: list[dict[str, Any]] = []
    for index, line in enumerate(sequence):
        start_word = boundaries[index]
        end_word = boundaries[index + 1] - 1
        if end_word < start_word:
            return display, {
                "applied": False,
                "reason": "word_timed_mantra_boundary_projection_failed",
                "review_required": True,
            }
        cue_start = int(words[start_word]["start_ms"])
        cue_end = int(words[end_word]["end_ms"])
        cycle_index, line_index = divmod(index, len(lines))
        cues.append(
            {
                "segment_id": (
                    f"mantra-word-c{cycle_index + 1:02d}-"
                    f"l{line_index + 1:02d}"
                ),
                "start_ms": cue_start,
                "end_ms": max(cue_start + 1, cue_end),
                "raw_text": "",
                "text": line,
                "corrected_text": line,
                "cleaned_text": line,
                "source_segment_ids": [],
                "source_word_start_index": start_word,
                "source_word_end_index": end_word,
                "cleanup_actions": ["canonical_mantra_word_boundary_projection"],
                "cleanup_review_reasons": [],
                "timing_source": "chirp_word_boundary_projection",
                "canonical_cycle_index": cycle_index + 1,
                "canonical_line_index": line_index + 1,
            }
        )

    region_start_ms = int(words[first_word]["start_ms"])
    region_end_ms = int(words[last_word]["end_ms"])
    kept = [
        item
        for item in display
        if int(item.get("end_ms") or 0) <= region_start_ms
        or int(item.get("start_ms") or 0) >= region_end_ms
    ]
    rendered = sorted(
        [*kept, *cues],
        key=lambda item: (
            int(item.get("start_ms") or 0),
            int(item.get("end_ms") or 0),
        ),
    )
    return rendered, {
        "applied": True,
        "match": "human_canonical_cycles_projected_to_chirp_word_boundaries",
        "review_required": False,
        "canonical_version": canonical.get("active_version"),
        "canonical_checksum": canonical.get("active_checksum"),
        "canonical_line_count": len(lines),
        "cycle_count": cycle_count,
        "input_pair_count": len(lines) * cycle_count,
        "output_line_count": len(cues),
        "whole_cycles_preserved": True,
        "leader_congregation_duplicate_removed": True,
        "timing_source": "chirp_word_boundary_projection",
        "source_start_ms": region_start_ms,
        "source_end_ms": region_end_ms,
        "source_start_word_index": first_word,
        "source_end_word_index": last_word,
        "publication_layer": "display_segments",
        "title_segment_id": title.get("segment_id"),
        "closing_segment_id": closing.get("segment_id"),
    }


def human_semantic_display_layer(
    source_segments: list[dict[str, Any]],
    human_text: str,
    omission_report: dict[str, Any],
    *,
    minimum_matched_chars: int = 3,
    minimum_coverage: float = 0.25,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Map human-gold semantic units back onto immutable source timing."""

    source_text, char_bounds = _source_char_bounds(source_segments)
    human_normalized, _ = _normalized_with_raw_map(human_text)
    units = _human_semantic_units(human_text)
    if not source_text or not human_normalized or not units:
        return deepcopy(source_segments), {
            "applied": False,
            "reason": "empty_source_or_human",
            "mapped_units": 0,
        }

    matcher = SequenceMatcher(None, source_text, human_normalized, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if int(block.size) > 0]

    def projected_source_range(
        human_start: int,
        human_end: int,
    ) -> tuple[int, int] | None:
        left_block = None
        right_block = None
        for block in blocks:
            block_h_start = int(block.b)
            block_h_end = block_h_start + int(block.size)
            if block_h_end <= human_start:
                left_block = block
                continue
            if block_h_start >= human_end:
                right_block = block
                break
        if left_block is None or right_block is None:
            return None
        human_gap_start = int(left_block.b) + int(left_block.size)
        human_gap_end = int(right_block.b)
        source_gap_start = int(left_block.a) + int(left_block.size)
        source_gap_end = int(right_block.a)
        human_gap_size = human_gap_end - human_gap_start
        source_gap_size = source_gap_end - source_gap_start
        if human_gap_size <= 0 or source_gap_size <= 0:
            return None
        if human_start < human_gap_start or human_end > human_gap_end:
            return None
        relative_start = (human_start - human_gap_start) / human_gap_size
        relative_end = (human_end - human_gap_start) / human_gap_size
        projected_start = source_gap_start + round(source_gap_size * relative_start)
        projected_end = source_gap_start + round(source_gap_size * relative_end)
        if projected_end <= projected_start:
            projected_end = min(source_gap_end, projected_start + 1)
        if projected_end <= projected_start:
            return None
        return projected_start, projected_end

    output: list[dict[str, Any]] = []
    unmapped_units: list[dict[str, Any]] = []
    mapped_chars = 0
    context_projected_units = 0
    for unit_index, unit in enumerate(units):
        human_start = int(unit["human_start"])
        human_end = int(unit["human_end"])
        source_ranges: list[tuple[int, int]] = []
        matched = 0
        for block in blocks:
            block_h_start = int(block.b)
            block_h_end = block_h_start + int(block.size)
            left = max(human_start, block_h_start)
            right = min(human_end, block_h_end)
            if right <= left:
                continue
            width = right - left
            source_start = int(block.a) + (left - block_h_start)
            source_ranges.append((source_start, source_start + width))
            matched += width
        coverage = matched / max(1, int(unit["normalized_chars"]))
        mapping_method = "exact_anchor"
        exact_supported = bool(source_ranges) and (
            (matched >= minimum_matched_chars and coverage >= minimum_coverage)
            or coverage >= 0.5
        )
        if not exact_supported:
            projected = projected_source_range(human_start, human_end)
            if projected is not None:
                source_ranges = [projected]
                mapping_method = "context_projection"
                context_projected_units += 1
            else:
                source_ranges = []
        if not source_ranges:
            unmapped_units.append(
                {
                    "text": str(unit["text"]),
                    "human_start": human_start,
                    "human_end": human_end,
                    "matched_chars": matched,
                    "coverage": round(coverage, 4),
                }
            )
            continue
        source_start = min(value[0] for value in source_ranges)
        source_end = max(value[1] for value in source_ranges)
        if source_start >= len(char_bounds) or source_end <= 0:
            continue
        source_end = min(source_end, len(char_bounds))
        start_ms = int(char_bounds[source_start][0])
        end_ms = int(char_bounds[source_end - 1][1])
        segment_indexes = sorted(
            {
                int(char_bounds[index][2])
                for index in range(source_start, source_end)
                if 0 <= index < len(char_bounds)
            }
        )
        source_ids = [
            str(source_segments[index].get("segment_id"))
            for index in segment_indexes
        ]
        output.append(
            {
                "segment_id": f"human-display-{unit_index + 1:04d}",
                "start_ms": start_ms,
                "end_ms": max(start_ms + 1, end_ms),
                "raw_text": "",
                "text": str(unit["text"]),
                "corrected_text": str(unit["text"]),
                "cleaned_text": str(unit["text"]),
                "source_segment_ids": source_ids,
                "cleanup_actions": ["human_semantic_alignment"],
                "cleanup_review_reasons": [],
                "human_alignment_coverage": round(coverage, 4),
                "human_mapping_method": mapping_method,
            }
        )
        mapped_chars += matched

    preserved_source_ids: set[str] = set()
    for candidate in omission_report.get("candidates", []):
        if candidate.get("direction") != "human_missing":
            continue
        left = int(candidate.get("start_ms") or 0)
        right = int(candidate.get("end_ms") or left)
        for source in source_segments:
            start = int(source.get("start_ms") or 0)
            end = int(source.get("end_ms") or start)
            if end <= left or start >= right:
                continue
            source_id = str(source.get("segment_id") or "")
            if not source_id or source_id in preserved_source_ids:
                continue
            preserved = deepcopy(source)
            preserved["corrected_text"] = _segment_text(source)
            preserved["cleaned_text"] = _segment_text(source)
            preserved.setdefault("source_segment_ids", [source_id])
            preserved.setdefault("cleanup_actions", []).append(
                "preserved_human_missing_source"
            )
            output.append(preserved)
            preserved_source_ids.add(source_id)

    output.sort(
        key=lambda item: (
            int(item.get("start_ms") or 0),
            int(item.get("end_ms") or 0),
            str(item.get("segment_id") or ""),
        )
    )
    monotonic: list[dict[str, Any]] = []
    overlap_merges = 0
    for item in output:
        if not monotonic:
            monotonic.append(item)
            continue
        previous = monotonic[-1]
        start = int(item.get("start_ms") or 0)
        end = int(item.get("end_ms") or start)
        previous_end = int(previous.get("end_ms") or 0)
        if start < previous_end:
            if str(item.get("segment_id") or "").startswith("human-display-"):
                previous["cleaned_text"] = (
                    _segment_text(previous).rstrip() + _segment_text(item).lstrip()
                )
                previous["corrected_text"] = previous["cleaned_text"]
                previous["end_ms"] = max(previous_end, end)
                previous.setdefault("source_segment_ids", []).extend(
                    value
                    for value in item.get("source_segment_ids", [])
                    if value not in previous.get("source_segment_ids", [])
                )
                previous.setdefault("cleanup_actions", []).append(
                    "merged_human_timing_overlap"
                )
                overlap_merges += 1
                continue
            item["start_ms"] = previous_end
            if int(item.get("end_ms") or 0) <= previous_end:
                item["end_ms"] = previous_end + 1
        monotonic.append(item)
    return monotonic, {
        "applied": bool(monotonic),
        "mapped_units": sum(
            str(item.get("segment_id") or "").startswith("human-display-")
            for item in monotonic
        ),
        "semantic_unit_count": len(units),
        "unmapped_unit_count": len(unmapped_units),
        "unmapped_units": unmapped_units,
        "mapped_char_evidence": mapped_chars,
        "human_normalized_chars": len(human_normalized),
        "context_projected_units": context_projected_units,
        "preserved_human_missing_source_cues": len(preserved_source_ids),
        "overlap_merges": overlap_merges,
    }


def _input_segments(srt_text: str) -> list[dict[str, Any]]:
    cues = parse_srt_text(srt_text)
    result: list[dict[str, Any]] = []
    for index, cue in enumerate(cues):
        text = str(cue["text"])
        segment_id = f"skill-source-{index + 1:04d}"
        result.append(
            {
                "segment_id": segment_id,
                "start_ms": int(cue["start_ms"]),
                "end_ms": int(cue["end_ms"]),
                "raw_text": text,
                "text": text,
                "corrected_text": text,
                "cleaned_text": text,
                "source_segment_ids": [segment_id],
                "cleanup_actions": [],
                "cleanup_review_reasons": [],
            }
        )
    return result


def _raw_fingerprint(segments: Iterable[dict[str, Any]]) -> str:
    rows = [
        (
            str(item.get("segment_id") or ""),
            int(item.get("start_ms") or 0),
            int(item.get("end_ms") or 0),
            str(item.get("raw_text") or item.get("text") or ""),
        )
        for item in segments
    ]
    return _sha256_bytes(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _load_error_memory(data_dir: Path) -> dict[str, Any]:
    path = data_dir / DEFAULT_ERROR_MEMORY_RELATIVE_PATH
    if not path.is_file():
        return {"entry_count": 0, "entries": [], "sha256": ""}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entry_count": 0, "entries": [], "sha256": ""}
    entries = value.get("entries", []) if isinstance(value, dict) else []
    return {
        "entry_count": len(entries) if isinstance(entries, list) else 0,
        "entries": entries if isinstance(entries, list) else [],
        "sha256": _sha256_bytes(path.read_bytes()),
    }


def blind_audit(
    segments: list[dict[str, Any]],
    *,
    data_dir: Path,
) -> dict[str, Any]:
    """Snapshot only pre-existing learning/canonical state, with no writes."""

    corpus_path = data_dir / DEFAULT_CORPUS_RELATIVE_PATH
    error_memory = _load_error_memory(data_dir)
    golden = audit_golden_variants(segments)
    joined = "\n".join(_segment_text(item) for item in segments)
    error_hits: list[dict[str, Any]] = []
    for entry in error_memory["entries"]:
        if not isinstance(entry, dict):
            continue
        variants = [
            str(value)
            for value in entry.get("variants", [])
            if str(value) and str(value) in joined
        ]
        if variants:
            error_hits.append(
                {
                    "canonical": str(entry.get("canonical") or ""),
                    "matched_variants": variants,
                    "confidence": str(entry.get("confidence") or ""),
                    "scope": str(entry.get("scope") or ""),
                }
            )
    scripture = active_canonical(data_dir, SCRIPTURE_KEY)
    mantra = active_canonical(data_dir, MANTRA_KEY)
    reference_items = [
        {"raw_text": _segment_text(item)}
        for item in segments
        if _segment_text(item).strip()
    ]
    corpus_reference = golden_corpus_reference(
        reference_items[: min(len(reference_items), 240)],
        data_dir,
        top_k=6,
        max_chars=2400,
    )
    return {
        "skill_version": SKILL_VERSION,
        "content_mode": CONTENT_MODE,
        "learning_policy": "blind_snapshot_before_current_lesson_alignment",
        "golden_ruleset_version": GOLDEN_RULESET_VERSION,
        "golden_rule_hits": golden,
        "golden_corpus_digest": corpus_digest(corpus_path),
        "golden_corpus_present": corpus_path.is_file(),
        "golden_corpus_reference": corpus_reference,
        "error_memory_sha256": error_memory["sha256"],
        "error_memory_entry_count": error_memory["entry_count"],
        "error_memory_hits": error_hits,
        "canonical_scripture": {
            "active_version": scripture.get("active_version") if scripture else None,
            "active_checksum": scripture.get("active_checksum") if scripture else None,
            "source": scripture.get("source") if scripture else None,
        },
        "canonical_mantra": {
            "active_version": mantra.get("active_version") if mantra else None,
            "active_checksum": mantra.get("active_checksum") if mantra else None,
        },
    }


def align_human_gold(
    source_segments: list[dict[str, Any]],
    human_text: str,
    *,
    minimum_coverage: float = 0.42,
    minimum_anchor_chars: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply human text only where ordered character evidence aligns it."""

    result = deepcopy(source_segments)
    source_text, spans = _source_stream(source_segments)
    human_normalized, human_raw_positions = _normalized_with_raw_map(human_text)
    if not source_text or not human_normalized:
        return result, {
            "applied": False,
            "aligned_cues": 0,
            "preserved_source_cues": len(result),
            "reason": "empty_source_or_human",
        }

    matcher = SequenceMatcher(None, source_text, human_normalized, autojunk=False)
    blocks = [
        block
        for block in matcher.get_matching_blocks()
        if int(block.size) >= minimum_anchor_chars
    ]
    aligned = 0
    changed = 0
    evidence: list[dict[str, Any]] = []
    for source_start, source_end, segment_index in spans:
        source_len = source_end - source_start
        if source_len < minimum_anchor_chars:
            continue
        mapped_ranges: list[tuple[int, int, int]] = []
        matched_chars = 0
        for block in blocks:
            left = max(source_start, int(block.a))
            right = min(source_end, int(block.a) + int(block.size))
            if right <= left:
                continue
            width = right - left
            target_start = int(block.b) + (left - int(block.a))
            target_end = target_start + width
            mapped_ranges.append((target_start, target_end, width))
            matched_chars += width
        coverage = matched_chars / max(1, source_len)
        if (
            coverage < minimum_coverage
            or matched_chars < minimum_anchor_chars
            or not mapped_ranges
        ):
            continue
        target_start = min(item[0] for item in mapped_ranges)
        target_end = max(item[1] for item in mapped_ranges)
        if target_end - target_start > max(source_len * 2 + 24, source_len + 60):
            continue
        raw_start = human_raw_positions[target_start]
        raw_end = (
            human_raw_positions[target_end]
            if target_end < len(human_raw_positions)
            else len(human_text)
        )
        replacement = human_text[raw_start:raw_end].strip()
        if not replacement:
            continue
        old_text = _segment_text(result[segment_index]).strip()
        result[segment_index]["corrected_text"] = replacement
        result[segment_index]["cleaned_text"] = replacement
        result[segment_index].setdefault("cleanup_actions", []).append(
            "human_aligned_gold"
        )
        result[segment_index]["human_alignment_coverage"] = round(coverage, 4)
        aligned += 1
        changed += int(replacement != old_text)
        evidence.append(
            {
                "segment_id": str(result[segment_index]["segment_id"]),
                "coverage": round(coverage, 4),
                "source_text": old_text,
                "human_text": replacement,
            }
        )
    return result, {
        "applied": aligned > 0,
        "aligned_cues": aligned,
        "changed_cues": changed,
        "preserved_source_cues": len(result) - aligned,
        "source_normalized_chars": len(source_text),
        "human_normalized_chars": len(human_normalized),
        "evidence": evidence,
    }



def _chirp_word_semantic_fallback(
    words: list[dict[str, Any]],
    first_word: int,
    last_word: int,
    *,
    segment_prefix: str,
) -> list[dict[str, Any]]:
    """Rebuild one locally unreliable human-alignment span from Chirp words.

    This is a fail-closed fallback for regions where several human semantic
    units collapse onto the same Chirp word and would otherwise create an
    impossible text-density cue.  It never synthesizes timestamps: every cue
    starts/ends on an existing Chirp word boundary.
    """
    if not (0 <= first_word <= last_word < len(words)):
        return []
    output: list[dict[str, Any]] = []
    cue_start_word = first_word
    cue_text_parts: list[str] = []
    strong_boundary = re.compile(r"[。！？!?；;][\"'」』）)]*$")
    soft_boundary = re.compile(r"[，,、：:][\"'」』）)]*$")

    def emit(end_word: int) -> None:
        nonlocal cue_start_word, cue_text_parts
        text = "".join(cue_text_parts).strip()
        text = (
            text.replace(",", "，")
            .replace("?", "？")
            .replace("!", "！")
            .replace(";", "；")
        )
        if not text:
            cue_start_word = end_word + 1
            cue_text_parts = []
            return
        start_ms = int(words[cue_start_word]["start_ms"])
        end_ms = int(words[end_word]["end_ms"])
        output.append(
            {
                "segment_id": f"{segment_prefix}-{len(output) + 1:03d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "raw_text": text,
                "text": text,
                "corrected_text": text,
                "cleaned_text": text,
                "source_segment_ids": [],
                "source_word_start_index": cue_start_word,
                "source_word_end_index": end_word,
                "cleanup_actions": ["chirp_word_semantic_fallback"],
                "cleanup_review_reasons": [
                    "human_alignment_same_word_collapse_fail_closed"
                ],
                "human_mapping_method": "chirp_word_fail_closed",
                "timing_source": "chirp_word_timestamps",
            }
        )
        cue_start_word = end_word + 1
        cue_text_parts = []

    for word_index in range(first_word, last_word + 1):
        raw_text = str(words[word_index].get("word") or "")
        cue_text_parts.append(raw_text)
        current_text = "".join(cue_text_parts)
        normalized, _ = _normalized_with_raw_map(current_text)
        start_ms = int(words[cue_start_word]["start_ms"])
        end_ms = int(words[word_index]["end_ms"])
        duration_ms = max(1, end_ms - start_ms)
        next_gap_ms = 0
        if word_index < last_word:
            next_gap_ms = max(
                0,
                int(words[word_index + 1]["start_ms"])
                - int(words[word_index]["end_ms"]),
            )
        boundary = False
        if strong_boundary.search(raw_text):
            boundary = True
        elif soft_boundary.search(raw_text) and (
            len(normalized) >= 14 or duration_ms >= 3_500
        ):
            boundary = True
        elif next_gap_ms >= 800 and len(normalized) >= 6:
            boundary = True
        elif duration_ms >= 6_000 or len(normalized) >= 24:
            boundary = True
        if boundary:
            emit(word_index)
    if cue_text_parts:
        emit(last_word)
    return output



def _apply_evidence_backed_golden_rules(
    segments: list[dict[str, Any]],
    *,
    human_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply Golden Rules without changing IDs or timestamps.

    High-confidence rules are deterministic.  Medium-confidence rules are
    applied only when the canonical spelling is explicitly present in the
    human reference transcript, so a Chirp fail-closed fallback can recover
    trusted terminology without inventing unsupported text.
    """
    output = deepcopy(segments)
    actions: list[dict[str, Any]] = []
    human = str(human_text or "")
    for term in golden_terms():
        confidence = str(term.get("confidence") or "medium")
        canonical = str(term.get("canonical") or "")
        if not canonical:
            continue
        if confidence != "high" and canonical not in human:
            continue
        variants = sorted(
            (str(value) for value in term.get("variants", []) if str(value)),
            key=len,
            reverse=True,
        )
        for item in output:
            before = _segment_text(item)
            after = before
            replaced: list[str] = []
            for variant in variants:
                if variant and variant in after:
                    after = after.replace(variant, canonical)
                    replaced.append(variant)
            if after == before:
                continue
            item["cleaned_text"] = after
            item["corrected_text"] = after
            item.setdefault("cleanup_actions", []).append(
                "golden_rule_canonicalized"
            )
            actions.append(
                {
                    "segment_id": str(item.get("segment_id") or ""),
                    "canonical": canonical,
                    "variants": replaced,
                    "confidence": confidence,
                    "timestamps_modified": False,
                }
            )
    return output, actions


def _text_density_anomalies(
    segments: list[dict[str, Any]],
    *,
    max_chars_per_second: float = 10.0,
    minimum_chars: int = 16,
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for item in segments:
        normalized, _ = _normalized_with_raw_map(_segment_text(item))
        if len(normalized) < minimum_chars:
            continue
        duration_ms = int(item.get("end_ms") or 0) - int(item.get("start_ms") or 0)
        if duration_ms <= 0:
            continue
        cps = len(normalized) * 1000.0 / duration_ms
        if cps <= max_chars_per_second:
            continue
        anomalies.append(
            {
                "segment_id": str(item.get("segment_id") or ""),
                "normalized_chars": len(normalized),
                "duration_ms": duration_ms,
                "chars_per_second": round(cps, 2),
            }
        )
    return anomalies


def _merge_short_cues(
    segments: list[dict[str, Any]],
    *,
    threshold_ms: int = 250,
    max_gap_ms: int = 1_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    work = deepcopy(segments)
    actions: list[dict[str, Any]] = []
    index = 0
    while index < len(work):
        item = work[index]
        duration = int(item["end_ms"]) - int(item["start_ms"])
        if duration >= threshold_ms:
            index += 1
            continue
        previous = work[index - 1] if index > 0 else None
        following = work[index + 1] if index + 1 < len(work) else None
        # Prefer extending a very short caption through the real following
        # silence instead of merging its text with another semantic unit.
        # The new end remains an existing Chirp word start boundary.
        if following is not None:
            following_gap_for_extension = (
                int(following["start_ms"]) - int(item["end_ms"])
            )
            normalized_short_text, _ = _normalized_with_raw_map(_segment_text(item))
            silence_extension_limit_ms = (
                6_000 if len(normalized_short_text) <= 4 else 1_500
            )
            if (
                0 <= following_gap_for_extension <= silence_extension_limit_ms
                and duration + following_gap_for_extension >= threshold_ms
            ):
                original_end = int(item["end_ms"])
                item["end_ms"] = int(following["start_ms"])
                item.setdefault("cleanup_actions", []).append(
                    "extended_pathological_short_cue_into_silence"
                )
                actions.append(
                    {
                        "segment_id": str(item.get("segment_id")),
                        "direction": "following_silence",
                        "duration_ms": duration,
                        "extended_by_ms": int(item["end_ms"]) - original_end,
                        "timing_source": "chirp_word_boundary_silence_extension",
                    }
                )
                index += 1
                continue
        previous_gap = (
            int(item["start_ms"]) - int(previous["end_ms"])
            if previous is not None
            else 10**9
        )
        following_gap = (
            int(following["start_ms"]) - int(item["end_ms"])
            if following is not None
            else 10**9
        )
        target = None
        direction = ""
        if previous is not None and 0 <= previous_gap <= max_gap_ms:
            target, direction = previous, "previous"
        elif following is not None and 0 <= following_gap <= max_gap_ms:
            target, direction = following, "following"
        if target is None:
            index += 1
            continue
        text = _segment_text(item).strip()
        if direction == "previous":
            target["end_ms"] = max(int(target["end_ms"]), int(item["end_ms"]))
            target["cleaned_text"] = (_segment_text(target).rstrip() + text).strip()
            target["corrected_text"] = target["cleaned_text"]
            target.setdefault("source_segment_ids", []).extend(
                item.get("source_segment_ids") or [str(item.get("segment_id"))]
            )
            removed = work.pop(index)
            target.setdefault("cleanup_actions", []).append(
                "merged_pathological_short_cue"
            )
            actions.append(
                {
                    "segment_id": str(removed.get("segment_id")),
                    "direction": direction,
                    "duration_ms": duration,
                }
            )
            continue
        target["start_ms"] = min(int(target["start_ms"]), int(item["start_ms"]))
        target["cleaned_text"] = (text + _segment_text(target).lstrip()).strip()
        target["corrected_text"] = target["cleaned_text"]
        target.setdefault("source_segment_ids", [])[:0] = (
            item.get("source_segment_ids") or [str(item.get("segment_id"))]
        )
        target.setdefault("cleanup_actions", []).append(
            "merged_pathological_short_cue"
        )
        removed = work.pop(index)
        actions.append(
            {
                "segment_id": str(removed.get("segment_id")),
                "direction": direction,
                "duration_ms": duration,
            }
        )
        index += 1
    return work, actions


def _split_long_cues(
    segments: list[dict[str, Any]],
    *,
    threshold_ms: int = 15_000,
    merged_words: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for item in segments:
        start = int(item["start_ms"])
        end = int(item["end_ms"])
        duration = end - start
        text = _segment_text(item).strip()
        parts = [part.strip() for part in _SEMANTIC_SPLIT_RE.split(text) if part.strip()]
        if duration <= threshold_ms or len(parts) < 2:
            output.append(deepcopy(item))
            continue
        if (
            str(item.get("timing_source") or "") == "chirp_word_timestamps"
            and merged_words is not None
        ):
            raw_words = (
                merged_words.get("words", [])
                if isinstance(merged_words, dict)
                else merged_words
            )
            if isinstance(raw_words, list):
                first_word = int(item.get("source_word_start_index") or 0)
                last_word = int(item.get("source_word_end_index") or first_word)
                if 0 <= first_word <= last_word < len(raw_words):
                    subset = {"words": raw_words[first_word : last_word + 1]}
                    word_split, split_meta = word_timed_human_display_layer(
                        [item],
                        text,
                        {"candidates": []},
                        subset,
                        max_unit_span_ms=threshold_ms,
                    )
                    if split_meta.get("applied") and len(word_split) >= 2:
                        for part_index, child in enumerate(word_split, 1):
                            child = deepcopy(child)
                            child["segment_id"] = (
                                f"{item['segment_id']}-word-semantic-{part_index:02d}"
                            )
                            child.setdefault("cleanup_actions", []).append(
                                "word_timed_long_cue_split"
                            )
                            output.append(child)
                        actions.append(
                            {
                                "segment_id": str(item.get("segment_id")),
                                "duration_ms": duration,
                                "output_parts": len(word_split),
                                "timing_source": "chirp_word_timestamps",
                            }
                        )
                        continue
        if merged_words is not None:
            # Chirp word timing is authoritative.  If a word-boundary split
            # cannot be proven, preserve the original cue instead of
            # synthesizing milliseconds from text length.
            preserved = deepcopy(item)
            preserved.setdefault("cleanup_review_reasons", []).append(
                "long_cue_word_boundary_split_unavailable"
            )
            output.append(preserved)
            actions.append(
                {
                    "segment_id": str(item.get("segment_id")),
                    "duration_ms": duration,
                    "output_parts": 1,
                    "timing_source": str(item.get("timing_source") or ""),
                    "preserved_without_proportional_fallback": True,
                }
            )
            continue
        weights = [max(1, len(_normalized_with_raw_map(part)[0])) for part in parts]
        total = sum(weights)
        cursor = start
        for part_index, (part, weight) in enumerate(zip(parts, weights)):
            part_end = (
                end
                if part_index == len(parts) - 1
                else min(end, cursor + round(duration * weight / total))
            )
            if part_end <= cursor:
                part_end = min(end, cursor + 1)
            child = deepcopy(item)
            child["segment_id"] = (
                f"{item['segment_id']}-semantic-{part_index + 1:02d}"
            )
            child["start_ms"] = cursor
            child["end_ms"] = part_end
            child["cleaned_text"] = part
            child["corrected_text"] = part
            child.setdefault("cleanup_actions", []).append(
                "semantic_long_cue_split"
            )
            child["timing_source"] = "legacy_text_length_proportional"
            output.append(child)
            cursor = part_end
        actions.append(
            {
                "segment_id": str(item.get("segment_id")),
                "duration_ms": duration,
                "output_parts": len(parts),
            }
        )
    return output, actions


def semantic_segmentation(
    segments: list[dict[str, Any]],
    *,
    merged_words: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    after_short, short_actions = _merge_short_cues(segments)
    after_long, long_actions = _split_long_cues(
        after_short,
        merged_words=merged_words,
    )
    return after_long, {
        "pathological_short_merges": short_actions,
        "long_semantic_splits": long_actions,
    }


def human_supported_mantra_display_layer(
    display_segments: list[dict[str, Any]],
    source_segments: list[dict[str, Any]],
    human_text: str,
    canonical: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Canonical mantra fallback using human cycle count + source anchors.

    The normal structural leader/response matcher remains first choice.  This
    fallback is only allowed when the human transcript contains one or more
    exact canonical cycles and the immutable source contains both the mantra
    title anchor and a clear post-mantra closing anchor.  It therefore maps
    canonical display text to real source time without guessing mantra words.
    """

    if not canonical:
        return display_segments, {
            "applied": False,
            "reason": "canonical_mantra_missing",
            "review_required": True,
        }
    body_text = str(canonical.get("body_text") or "")
    lines = [
        line.strip()
        for line in body_text.splitlines()
        if _normalized_with_raw_map(line.strip())[0]
    ]
    canonical_body = "".join(_normalized_with_raw_map(line)[0] for line in lines)
    human_normalized, _ = _normalized_with_raw_map(human_text)
    if not canonical_body or not human_normalized:
        return display_segments, {
            "applied": False,
            "reason": "canonical_or_human_empty",
            "review_required": True,
        }
    cycle_count = human_normalized.count(canonical_body)
    if cycle_count <= 0:
        return display_segments, {
            "applied": False,
            "reason": "human_exact_canonical_cycle_missing",
            "review_required": True,
        }

    title_index = next(
        (
            index
            for index, item in enumerate(source_segments)
            if "得見彌勒根本大明神咒" in _segment_text(item)
        ),
        None,
    )
    if title_index is None:
        return display_segments, {
            "applied": False,
            "reason": "source_mantra_title_anchor_missing",
            "review_required": True,
            "human_cycle_count": cycle_count,
        }
    closing_index = next(
        (
            index
            for index in range(title_index + 1, len(source_segments))
            if "大眾請起" in _segment_text(source_segments[index])
            or "大眾起立" in _segment_text(source_segments[index])
            or "向佛行" in _segment_text(source_segments[index])
        ),
        None,
    )
    if closing_index is None or closing_index <= title_index + 1:
        return display_segments, {
            "applied": False,
            "reason": "source_mantra_closing_anchor_missing",
            "review_required": True,
            "human_cycle_count": cycle_count,
        }
    title_source = source_segments[title_index]
    body_last_source = source_segments[closing_index - 1]
    title_start = int(title_source.get("start_ms") or 0)
    body_start = int(title_source.get("end_ms") or title_start)
    body_end = int(body_last_source.get("end_ms") or body_start)
    if body_end <= body_start:
        return display_segments, {
            "applied": False,
            "reason": "source_mantra_timing_invalid",
            "review_required": True,
            "human_cycle_count": cycle_count,
        }

    weights = [max(1, len(_normalized_with_raw_map(line)[0])) for line in lines]
    total_weight = sum(weights) * cycle_count
    if total_weight <= 0:
        return display_segments, {
            "applied": False,
            "reason": "canonical_mantra_weight_invalid",
            "review_required": True,
        }

    replacement: list[dict[str, Any]] = [
        {
            "segment_id": "mantra-display-title",
            "start_ms": title_start,
            "end_ms": body_start,
            "raw_text": "",
            "text": MANTRA_TITLE,
            "corrected_text": MANTRA_TITLE,
            "cleaned_text": MANTRA_TITLE,
            "source_segment_ids": [str(title_source.get("segment_id") or "")],
            "cleanup_actions": ["canonical_mantra_title"],
            "cleanup_review_reasons": [],
        }
    ]
    total_duration = body_end - body_start
    elapsed_weight = 0
    line_counter = 0
    for cycle in range(cycle_count):
        for line, weight in zip(lines, weights):
            line_start = body_start + round(total_duration * elapsed_weight / total_weight)
            elapsed_weight += weight
            line_end = body_start + round(total_duration * elapsed_weight / total_weight)
            line_counter += 1
            replacement.append(
                {
                    "segment_id": (
                        f"mantra-display-c{cycle + 1:02d}-l{line_counter:03d}"
                    ),
                    "start_ms": line_start,
                    "end_ms": max(line_start + 1, line_end),
                    "raw_text": "",
                    "text": line,
                    "corrected_text": line,
                    "cleaned_text": line,
                    "source_segment_ids": [
                        str(source_segments[index].get("segment_id") or "")
                        for index in range(title_index, closing_index)
                    ],
                    "cleanup_actions": ["canonical_mantra_human_supported"],
                    "cleanup_review_reasons": [],
                }
            )
    kept = [
        deepcopy(item)
        for item in display_segments
        if int(item.get("end_ms") or 0) <= title_start
        or int(item.get("start_ms") or 0) >= body_end
    ]
    combined = kept + replacement
    combined.sort(
        key=lambda item: (
            int(item.get("start_ms") or 0),
            int(item.get("end_ms") or 0),
            str(item.get("segment_id") or ""),
        )
    )
    return combined, {
        "applied": True,
        "match": "human_exact_cycles_with_source_title_closing_anchors",
        "review_required": False,
        "canonical_version": canonical.get("active_version"),
        "canonical_checksum": canonical.get("active_checksum"),
        "cycle_count": cycle_count,
        "expected_pair_count": len(lines),
        "output_line_count": line_counter,
        "whole_cycles_preserved": True,
        "source_title_segment_id": str(title_source.get("segment_id") or ""),
        "source_closing_segment_id": str(
            source_segments[closing_index].get("segment_id") or ""
        ),
        "source_start_ms": title_start,
        "body_start_ms": body_start,
        "source_end_ms": body_end,
        "evidence": {
            "human_exact_cycle_count": cycle_count,
            "canonical_line_count": len(lines),
            "source_anchor_count": 2,
        },
    }


def _candidate_differences(
    source_segments: list[dict[str, Any]],
    reviewed_segments: list[dict[str, Any]],
    *,
    canonical_text: str,
    ignored_source_ids: set[str] | None = None,
    limit: int = 80,
) -> list[dict[str, Any]]:
    ignored_source_ids = ignored_source_ids or set()
    reviewed_by_source: dict[str, str] = {}
    for item in reviewed_segments:
        text = _segment_text(item)
        for source_id in item.get("source_segment_ids") or [item.get("segment_id")]:
            reviewed_by_source.setdefault(str(source_id), text)
    raw_candidates: list[tuple[str, str, str]] = []
    for source in source_segments:
        source_id = str(source.get("segment_id"))
        if source_id in ignored_source_ids:
            continue
        target = reviewed_by_source.get(source_id)
        if not target:
            continue
        left, _ = _normalized_with_raw_map(_segment_text(source))
        right, _ = _normalized_with_raw_map(target)
        if not left or not right or left == right:
            continue
        matcher = SequenceMatcher(None, left, right, autojunk=False)
        for tag, a1, a2, b1, b2 in matcher.get_opcodes():
            if tag != "replace":
                continue
            before = left[a1:a2]
            after = right[b1:b2]
            if not (1 <= len(before) <= 16 and 2 <= len(after) <= 16):
                continue
            if before == after or before.isdigit() or after.isdigit():
                continue
            raw_candidates.append((before, after, source_id))
    counts = Counter((before, after) for before, after, _ in raw_candidates)
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for before, after, source_id in raw_candidates:
        key = (before, after)
        if key in seen:
            continue
        seen.add(key)
        support = counts[key]
        canonical_hit = after in canonical_text if canonical_text else False
        buddhist_hint = bool(_BUDDHIST_HINT_RE.search(after))
        confidence = (
            "high"
            if canonical_hit
            else "medium"
            if buddhist_hint or support >= 2
            else "low"
        )
        results.append(
            {
                "variant": before,
                "canonical_candidate": after,
                "confidence": confidence,
                "support_count": support,
                "canonical_scripture_support": canonical_hit,
                "buddhist_term_hint": buddhist_hint,
                "example_segment_id": source_id,
                "promotion_policy": "candidate_only_requires_human_approval",
            }
        )
    order = {"high": 0, "medium": 1, "low": 2}
    results.sort(
        key=lambda item: (
            order[str(item["confidence"])],
            -int(item["support_count"]),
            str(item["canonical_candidate"]),
        )
    )
    return results[:limit]


def qa_review(
    segments: list[dict[str, Any]],
    *,
    source_segments: list[dict[str, Any]],
    original_raw_fingerprint: str,
    omission_report: dict[str, Any],
    scripture_alignment: dict[str, Any],
    mantra_alignment: dict[str, Any],
) -> dict[str, Any]:
    durations = [
        int(item.get("end_ms") or 0) - int(item.get("start_ms") or 0)
        for item in segments
    ]
    overlaps = [
        {
            "before": str(before.get("segment_id")),
            "after": str(after.get("segment_id")),
            "ms": int(before.get("end_ms") or 0)
            - int(after.get("start_ms") or 0),
        }
        for before, after in zip(segments, segments[1:])
        if int(after.get("start_ms") or 0) < int(before.get("end_ms") or 0)
    ]
    regressions = [
        str(after.get("segment_id"))
        for before, after in zip(segments, segments[1:])
        if int(after.get("start_ms") or 0) < int(before.get("start_ms") or 0)
    ]
    blank = [
        str(item.get("segment_id"))
        for item in segments
        if not _segment_text(item).strip()
    ]
    duplicates = [
        {
            "before": str(before.get("segment_id")),
            "after": str(after.get("segment_id")),
            "text": _segment_text(after).strip(),
        }
        for before, after in zip(segments, segments[1:])
        if _segment_text(before).strip()
        and _segment_text(before).strip() == _segment_text(after).strip()
    ]
    short = [
        str(item.get("segment_id"))
        for item, duration in zip(segments, durations)
        if duration < 250
    ]
    long = [
        str(item.get("segment_id"))
        for item, duration in zip(segments, durations)
        if duration > 15_000
    ]
    boundary = scan_boundary_integrity(segments)
    density_anomalies = _text_density_anomalies(segments)
    raw_fingerprint_after = _raw_fingerprint(source_segments)
    human_missing = [
        item
        for item in omission_report.get("candidates", [])
        if item.get("direction") == "human_missing"
    ]
    source_missing = [
        item
        for item in omission_report.get("candidates", [])
        if item.get("direction") == "source_missing"
    ]
    errors: list[str] = []
    if regressions:
        errors.append(f"timestamp_start_regression={len(regressions)}")
    if overlaps:
        errors.append(f"overlap={len(overlaps)}")
    if blank:
        errors.append(f"blank_cue={len(blank)}")
    if short:
        errors.append(f"pathological_lt250ms={len(short)}")
    if long:
        errors.append(f"long_gt15s={len(long)}")
    if density_anomalies:
        errors.append(f"text_density_gt10cps={len(density_anomalies)}")
    if raw_fingerprint_after != original_raw_fingerprint:
        errors.append("raw_immutability_violation")
    return {
        "skill_version": SKILL_VERSION,
        "status": "PASS" if not errors else "NEEDS_REVIEW",
        "errors": errors,
        "cue_count": len(segments),
        "median_duration_ms": statistics.median(durations) if durations else 0,
        "max_duration_ms": max(durations, default=0),
        "gt_15s_count": len(long),
        "gt_15s_segment_ids": long,
        "lt_250ms_count": len(short),
        "lt_250ms_segment_ids": short,
        "timestamp_regression_count": len(regressions),
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "blank_cue_count": len(blank),
        "adjacent_duplicate_count": len(duplicates),
        "adjacent_duplicates": duplicates,
        "boundary_integrity": boundary,
        "text_density_anomaly_count": len(density_anomalies),
        "text_density_anomalies": density_anomalies,
        "formal_scripture_alignment": scripture_alignment,
        "mantra": {
            "applied": bool(mantra_alignment.get("applied")),
            "cycle_count": int(mantra_alignment.get("cycle_count") or 0),
            "cue_count": int(mantra_alignment.get("output_line_count") or 0),
            "whole_cycles_preserved": bool(
                mantra_alignment.get("whole_cycles_preserved")
            ),
            "detail": mantra_alignment,
        },
        "human_missing_count": len(human_missing),
        "source_missing_count": len(source_missing),
        "human_missing": human_missing,
        "source_missing": source_missing,
        "raw_immutability": {
            "before": original_raw_fingerprint,
            "after": raw_fingerprint_after,
            "unchanged": raw_fingerprint_after == original_raw_fingerprint,
        },
    }


def srt_from_segments(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, item in enumerate(segments, 1):
        blocks.append(
            f"{index}\n"
            f"{_srt_time(int(item['start_ms']))} --> "
            f"{_srt_time(int(item['end_ms']))}\n"
            f"{_segment_text(item).strip()}"
        )
    return "\n\n".join(blocks) + "\n"


def review_lesson(
    *,
    srt_text: str,
    human_text: str,
    data_dir: Path,
    lesson_id: str,
    lesson_date: str,
    merged_words: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_segments = _input_segments(srt_text)
    if not source_segments:
        raise ValueError("input SRT contains no valid cues")
    if not human_text.strip():
        raise ValueError("human transcript is empty")
    original_raw_fingerprint = _raw_fingerprint(source_segments)

    blind = blind_audit(source_segments, data_dir=data_dir)
    omission = detect_bidirectional_omissions(source_segments, human_text)
    human_aligned, human_alignment = align_human_gold(source_segments, human_text)
    if merged_words is not None:
        human_display, human_display_meta = sequential_word_timed_human_display_layer(
            source_segments,
            human_text,
            omission,
            merged_words,
        )
        if not human_display_meta.get("applied"):
            human_display, legacy_meta = human_semantic_display_layer(
                source_segments,
                human_text,
                omission,
            )
            legacy_meta["legacy_fallback"] = True
            legacy_meta["fallback_reason"] = human_display_meta.get("reason")
            legacy_meta["timing_source"] = "legacy_srt_cue_proportional_fallback"
            human_display_meta = legacy_meta
    else:
        human_display, human_display_meta = human_semantic_display_layer(
            source_segments,
            human_text,
            omission,
        )
        human_display_meta["legacy_fallback"] = True
        human_display_meta["fallback_reason"] = "merged_words_not_provided"
        human_display_meta["timing_source"] = "legacy_srt_cue_proportional_fallback"

    scripture = active_canonical(data_dir, SCRIPTURE_KEY)
    mantra = active_canonical(data_dir, MANTRA_KEY)
    if merged_words is not None:
        scripture_display, scripture_meta = word_timed_scripture_verification_layer(
            human_display,
            scripture,
        )
        mantra_display, mantra_meta = word_timed_mantra_region_layer(
            scripture_display,
            human_text,
            mantra,
            merged_words,
        )
    else:
        scripture_display, scripture_meta = scripture_display_layer(
            human_display, scripture
        )
        mantra_display, mantra_meta = mantra_pair_display_layer(
            scripture_display, mantra
        )
        if not mantra_meta.get("applied"):
            mantra_display, human_mantra_meta = human_supported_mantra_display_layer(
                scripture_display,
                source_segments,
                human_text,
                mantra,
            )
            if human_mantra_meta.get("applied"):
                mantra_meta = human_mantra_meta
    final_segments, segmentation = semantic_segmentation(
        mantra_display,
        merged_words=merged_words,
    )
    final_segments, golden_canonicalization = _apply_evidence_backed_golden_rules(
        final_segments,
        human_text=human_text,
    )

    canonical_text = str(scripture.get("body_text") or "") if scripture else ""
    ignored_candidate_source_ids: set[str] = set()
    if mantra_meta.get("applied"):
        mantra_start = int(mantra_meta.get("source_start_ms") or -1)
        mantra_end = int(mantra_meta.get("source_end_ms") or -1)
        if mantra_start >= 0 and mantra_end > mantra_start:
            ignored_candidate_source_ids = {
                str(item.get("segment_id"))
                for item in source_segments
                if int(item.get("end_ms") or 0) > mantra_start
                and int(item.get("start_ms") or 0) < mantra_end
            }
    candidates = _candidate_differences(
        source_segments,
        final_segments,
        canonical_text=canonical_text,
        ignored_source_ids=ignored_candidate_source_ids,
    )
    residual_golden = audit_golden_variants(final_segments)
    qa = qa_review(
        final_segments,
        source_segments=source_segments,
        original_raw_fingerprint=original_raw_fingerprint,
        omission_report=omission,
        scripture_alignment=scripture_meta,
        mantra_alignment=mantra_meta,
    )
    qa["golden_rules"] = {
        "ruleset_version": GOLDEN_RULESET_VERSION,
        "blind_hit_count": int(
            blind.get("golden_rule_hits", {}).get("issue_count") or 0
        ),
        "blind_hits": blind.get("golden_rule_hits", {}).get("issues", []),
        "residual_hit_count": int(residual_golden.get("issue_count") or 0),
        "residual_hits": residual_golden.get("issues", []),
    }
    qa["new_golden_candidates"] = candidates
    qa["new_golden_candidate_counts"] = dict(
        Counter(str(item["confidence"]) for item in candidates)
    )
    return {
        "schema_version": 1,
        "skill_version": SKILL_VERSION,
        "content_mode": CONTENT_MODE,
        "lesson_id": str(lesson_id),
        "lesson_date": str(lesson_date),
        "learning_applied": False,
        "learning_gate": "requires_explicit_post_review_approval",
        "input_srt_sha256": _sha256_bytes(srt_text.encode("utf-8")),
        "human_transcript_sha256": _sha256_bytes(human_text.encode("utf-8")),
        "timing_policy": {
            "ordinary_speech": str(
                human_display_meta.get("timing_source")
                or "legacy_srt_cue_proportional_fallback"
            ),
            "chirp_word_timestamps_are_timing_truth": bool(
                human_display_meta.get("timing_source")
                == "chirp_word_timestamps"
            ),
            "human_transcript_is_text_truth": True,
        },
        "blind_audit": blind,
        "human_alignment": human_alignment,
        "human_semantic_display": human_display_meta,
        "omission_detection": omission,
        "scripture_alignment": scripture_meta,
        "mantra_alignment": mantra_meta,
        "semantic_segmentation": segmentation,
        "golden_rule_canonicalization": {
            "ruleset_version": GOLDEN_RULESET_VERSION,
            "applied_count": len(golden_canonicalization),
            "actions": golden_canonicalization,
            "timestamps_modified": False,
        },
        "qa": qa,
        "segments": final_segments,
        "srt": srt_from_segments(final_segments),
    }


def write_review_bundle(
    result: dict[str, Any],
    *,
    output_dir: Path,
    stem: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / f"{stem}.srt"
    qa_path = output_dir / f"{stem}_qa.json"
    completeness_path = output_dir / f"{stem}_completeness.txt"
    zip_path = output_dir / f"{stem}.zip"
    srt_path.write_text(str(result["srt"]), encoding="utf-8")
    report = {
        key: value
        for key, value in result.items()
        if key not in {"segments", "srt"}
    }
    qa_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    qa = result["qa"]
    completeness_path.write_text(
        "\n".join(
            [
                f"skill_version={result['skill_version']}",
                f"lesson_id={result['lesson_id']}",
                f"lesson_date={result['lesson_date']}",
                f"qa_status={qa['status']}",
                f"cues={qa['cue_count']}",
                f"median_duration_ms={qa['median_duration_ms']}",
                f"max_duration_ms={qa['max_duration_ms']}",
                f"gt_15s={qa['gt_15s_count']}",
                f"lt_250ms={qa['lt_250ms_count']}",
                f"mantra_cycles={qa['mantra']['cycle_count']}",
                f"mantra_cues={qa['mantra']['cue_count']}",
                f"human_missing={qa['human_missing_count']}",
                f"source_missing={qa['source_missing_count']}",
                f"golden_rules_blind_hits={qa['golden_rules']['blind_hit_count']}",
                f"golden_rules_residual_hits={qa['golden_rules']['residual_hit_count']}",
                f"learning_applied={str(result['learning_applied']).lower()}",
                f"raw_immutable={str(qa['raw_immutability']['unchanged']).lower()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(srt_path, arcname=srt_path.name)
        archive.write(qa_path, arcname=qa_path.name)
        archive.write(completeness_path, arcname=completeness_path.name)
    return {
        "srt": str(srt_path),
        "qa": str(qa_path),
        "completeness": str(completeness_path),
        "zip": str(zip_path),
    }
