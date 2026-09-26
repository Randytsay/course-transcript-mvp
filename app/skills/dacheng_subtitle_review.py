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

from app.canonical.alignment import mantra_pair_display_layer, scripture_display_layer
from app.canonical.defaults import MANTRA_KEY, MANTRA_TITLE, SCRIPTURE_KEY
from app.canonical.golden_corpus import (
    DEFAULT_CORPUS_RELATIVE_PATH,
    DEFAULT_ERROR_MEMORY_RELATIVE_PATH,
    corpus_digest,
    golden_corpus_reference,
    parse_srt_text,
)
from app.canonical.golden_rules import GOLDEN_RULESET_VERSION, audit_golden_variants
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
    """Return normalized source text plus an approximate time box per char."""

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


def _human_semantic_units(value: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    cursor = 0
    for paragraph in re.split(r"\n+", str(value or "")):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
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
                }
            )
    return units


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


def _merge_short_cues(
    segments: list[dict[str, Any]],
    *,
    threshold_ms: int = 250,
    max_gap_ms: int = 500,
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    after_short, short_actions = _merge_short_cues(segments)
    after_long, long_actions = _split_long_cues(after_short)
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
    human_display, human_display_meta = human_semantic_display_layer(
        source_segments,
        human_text,
        omission,
    )

    scripture = active_canonical(data_dir, SCRIPTURE_KEY)
    scripture_display, scripture_meta = scripture_display_layer(
        human_display, scripture
    )
    mantra = active_canonical(data_dir, MANTRA_KEY)
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
    final_segments, segmentation = semantic_segmentation(mantra_display)

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
        "blind_audit": blind,
        "human_alignment": human_alignment,
        "human_semantic_display": human_display_meta,
        "omission_detection": omission,
        "scripture_alignment": scripture_meta,
        "mantra_alignment": mantra_meta,
        "semantic_segmentation": segmentation,
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
