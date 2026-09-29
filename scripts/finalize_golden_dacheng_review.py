from __future__ import annotations

"""Finalize a Dacheng review using only existing Chirp word boundaries.

This is a deterministic display-layer finalizer.  It never invents timestamps:
long semantic cues may be split only at an existing Chirp word boundary, and a
dense cue may be merged with an adjacent cue only by reusing the two cues'
existing outer word-boundary timestamps.
"""

import argparse
import difflib
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.canonical.golden_rules import GOLDEN_RULESET_VERSION, audit_golden_variants
from app.skills import dacheng_subtitle_review as d


_NORMAL_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]")
_PUNCTUATION = "，、；：。！？"


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if _NORMAL_RE.fullmatch(ch))


def _words(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = payload.get("words", []) if isinstance(payload, dict) else payload
    return [x for x in raw if isinstance(x, dict)]


def _raw_split_candidates(text: str) -> list[int]:
    result: list[int] = []
    for i, ch in enumerate(text, 1):
        if ch in _PUNCTUATION and 0 < i < len(text):
            result.append(i)
    return result


def _normalized_prefix_length(text: str, raw_index: int) -> int:
    return len(_norm(text[:raw_index]))


def _target_to_source_char(source: str, target: str, target_pos: int) -> int:
    matcher = difflib.SequenceMatcher(None, source, target, autojunk=False)
    anchors: list[tuple[int, int]] = [(0, 0)]
    for block in matcher.get_matching_blocks():
        if block.size:
            anchors.append((int(block.b), int(block.a)))
            anchors.append((int(block.b + block.size), int(block.a + block.size)))
    anchors.append((len(target), len(source)))
    merged: list[tuple[int, int]] = []
    for t, s in sorted(anchors):
        if merged and merged[-1][0] == t:
            merged[-1] = (t, max(merged[-1][1], s))
        else:
            merged.append((t, s))
    monotonic: list[tuple[int, int]] = []
    previous = 0
    for t, s in merged:
        s = max(previous, min(len(source), s))
        monotonic.append((t, s))
        previous = s
    if target_pos <= 0:
        return 0
    if target_pos >= len(target):
        return len(source)
    left = 0
    right = len(monotonic) - 1
    while left + 1 < right:
        middle = (left + right) // 2
        if monotonic[middle][0] <= target_pos:
            left = middle
        else:
            right = middle
    t0, s0 = monotonic[left]
    t1, s1 = monotonic[right]
    if t1 == t0:
        return s0
    return round(s0 + (target_pos - t0) * (s1 - s0) / (t1 - t0))


def _split_long(
    segment: dict[str, Any],
    words: list[dict[str, Any]],
    *,
    max_ms: int = 15_000,
) -> list[dict[str, Any]]:
    start = int(segment["start_ms"])
    end = int(segment["end_ms"])
    if end - start <= max_ms:
        return [segment]
    first = int(segment.get("source_word_start_index", -1))
    last = int(segment.get("source_word_end_index", -1))
    if first < 0 or last <= first or last >= len(words):
        return [segment]
    text = d._segment_text(segment)
    candidates = _raw_split_candidates(text)
    if not candidates:
        return [segment]
    target_norm = _norm(text)
    word_norms = [_norm(str(words[i].get("word") or "")) for i in range(first, last + 1)]
    source_norm = "".join(word_norms)
    if not source_norm or not target_norm:
        return [segment]
    boundaries: list[int] = [0]
    for value in word_norms:
        boundaries.append(boundaries[-1] + len(value))
    midpoint = len(target_norm) / 2
    candidates.sort(key=lambda raw: abs(_normalized_prefix_length(text, raw) - midpoint))
    for raw_split in candidates:
        target_split = _normalized_prefix_length(text, raw_split)
        source_split = _target_to_source_char(source_norm, target_norm, target_split)
        # Pick the nearest *existing* boundary between two Chirp words.
        local_boundary = min(
            range(1, len(boundaries) - 1),
            key=lambda i: abs(boundaries[i] - source_split),
        )
        split_word = first + local_boundary
        if not (first < split_word <= last):
            continue
        left_end = int(words[split_word - 1].get("end_ms") or 0)
        right_start = int(words[split_word].get("start_ms") or 0)
        if not (start < left_end <= right_start < end):
            continue
        left_text = text[:raw_split].strip()
        right_text = text[raw_split:].strip()
        if not left_text or not right_text:
            continue
        left = deepcopy(segment)
        right = deepcopy(segment)
        left["segment_id"] = f"{segment['segment_id']}-a"
        right["segment_id"] = f"{segment['segment_id']}-b"
        left["end_ms"] = left_end
        right["start_ms"] = right_start
        left["source_word_end_index"] = split_word - 1
        right["source_word_start_index"] = split_word
        for item, value in ((left, left_text), (right, right_text)):
            item["text"] = value
            item["corrected_text"] = value
            item["cleaned_text"] = value
            item.setdefault("cleanup_actions", []).append(
                "golden_finalizer_split_at_chirp_word_boundary"
            )
            item["timing_source"] = "chirp_word_timestamps"
        result: list[dict[str, Any]] = []
        for item in (left, right):
            result.extend(_split_long(item, words, max_ms=max_ms))
        return result
    return [segment]


def _density_cps(segment: dict[str, Any]) -> float:
    duration = max(1, int(segment["end_ms"]) - int(segment["start_ms"]))
    return len(_norm(d._segment_text(segment))) * 1000 / duration


def _merge_dense_with_next(
    segments: list[dict[str, Any]],
    *,
    threshold_cps: float = 10.0,
    max_gap_ms: int = 1000,
    max_duration_ms: int = 15_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    i = 0
    while i < len(segments):
        current = deepcopy(segments[i])
        if _density_cps(current) <= threshold_cps or i + 1 >= len(segments):
            out.append(current)
            i += 1
            continue
        following = deepcopy(segments[i + 1])
        gap = int(following["start_ms"]) - int(current["end_ms"])
        duration = int(following["end_ms"]) - int(current["start_ms"])
        combined_text = d._segment_text(current).rstrip() + d._segment_text(following).lstrip()
        combined_cps = len(_norm(combined_text)) * 1000 / max(1, duration)
        if (
            0 <= gap <= max_gap_ms
            and duration <= max_duration_ms
            and combined_cps <= threshold_cps
        ):
            merged = deepcopy(current)
            merged["segment_id"] = f"{current['segment_id']}+{following['segment_id']}"
            merged["end_ms"] = int(following["end_ms"])
            merged["source_word_end_index"] = following.get(
                "source_word_end_index", merged.get("source_word_end_index")
            )
            merged["source_segment_ids"] = list(
                dict.fromkeys(
                    list(current.get("source_segment_ids") or [])
                    + list(following.get("source_segment_ids") or [])
                )
            )
            merged["text"] = combined_text
            merged["corrected_text"] = combined_text
            merged["cleaned_text"] = combined_text
            merged.setdefault("cleanup_actions", []).append(
                "golden_finalizer_dense_merge_existing_outer_word_boundaries"
            )
            merged["timing_source"] = "chirp_word_timestamps"
            out.append(merged)
            actions.append(
                {
                    "left": current["segment_id"],
                    "right": following["segment_id"],
                    "gap_ms": gap,
                    "combined_cps": round(combined_cps, 2),
                }
            )
            i += 2
            continue
        out.append(current)
        i += 1
    return out, actions


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-srt", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--merged-words", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--lesson-date", required=True)
    parser.add_argument("--stem", required=True)
    args = parser.parse_args()

    source_text = args.source_srt.read_text(encoding="utf-8")
    human_text = args.reference.read_text(encoding="utf-8-sig", errors="replace")
    merged_payload = json.loads(args.merged_words.read_text(encoding="utf-8"))
    base = d.review_lesson(
        srt_text=source_text,
        human_text=human_text,
        data_dir=args.data_dir,
        lesson_id=args.lesson_id,
        lesson_date=args.lesson_date,
        merged_words=merged_payload,
    )
    words = _words(merged_payload)
    segments: list[dict[str, Any]] = []
    split_actions: list[str] = []
    for item in base["segments"]:
        pieces = _split_long(deepcopy(item), words)
        if len(pieces) > 1:
            split_actions.append(str(item["segment_id"]))
        segments.extend(pieces)
    segments, dense_actions = _merge_dense_with_next(segments)

    source_segments = d._input_segments(source_text)
    qa = d.qa_review(
        segments,
        source_segments=source_segments,
        original_raw_fingerprint=d._raw_fingerprint(source_segments),
        omission_report=base["omission_detection"],
        scripture_alignment=base["scripture_alignment"],
        mantra_alignment=base["mantra_alignment"],
    )
    residual = audit_golden_variants(segments)
    qa["golden_rules"] = {
        "ruleset_version": GOLDEN_RULESET_VERSION,
        "residual_hit_count": int(residual.get("issue_count") or 0),
        "residual_hits": residual.get("issues", []),
    }
    qa["finalizer"] = {
        "long_split_source_segment_ids": split_actions,
        "dense_merge_actions": dense_actions,
        "timestamp_policy": "existing_chirp_word_boundaries_only",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = args.output_dir / f"{args.stem}.srt"
    transcript_path = args.output_dir / f"{args.stem}_逐字稿.txt"
    report_path = args.output_dir / f"{args.stem}_transcript_report.json"
    srt_path.write_text(d.srt_from_segments(segments), encoding="utf-8")
    transcript_path.write_text(
        "\n".join(d._segment_text(item).strip() for item in segments) + "\n",
        encoding="utf-8",
    )
    report = {
        "schema_version": 1,
        "workflow": "golden-course-transcript-v1.0",
        "lesson_id": args.lesson_id,
        "lesson_date": args.lesson_date,
        "timing_truth": "chirp_3_word_timestamps",
        "reference_sha256": hashlib.sha256(human_text.encode("utf-8")).hexdigest(),
        "source_srt_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "qa": qa,
        "base_review": {
            "skill_version": base["skill_version"],
            "timing_policy": base["timing_policy"],
            "semantic_segmentation": base["semantic_segmentation"],
            "golden_rule_canonicalization": base["golden_rule_canonicalization"],
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["outputs"] = {
        "srt": {"path": str(srt_path), "sha256": _sha(srt_path)},
        "transcript": {"path": str(transcript_path), "sha256": _sha(transcript_path)},
        "report": {"path": str(report_path), "sha256": _sha(report_path)},
    }
    print(
        json.dumps(
            {
                "status": qa["status"],
                "cue_count": qa["cue_count"],
                "errors": qa["errors"],
                "gt_15s_count": qa["gt_15s_count"],
                "lt_250ms_count": qa["lt_250ms_count"],
                "overlap_count": qa["overlap_count"],
                "blank_cue_count": qa["blank_cue_count"],
                "text_density_anomaly_count": qa["text_density_anomaly_count"],
                "human_missing_count": qa["human_missing_count"],
                "source_missing_count": qa["source_missing_count"],
                "golden_residual_hits": qa["golden_rules"]["residual_hit_count"],
                "finalizer": qa["finalizer"],
                "outputs": report["outputs"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if qa["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
