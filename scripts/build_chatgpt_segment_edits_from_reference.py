from __future__ import annotations

"""Build timestamp-free ChatGPT handoff edits from a same-lesson reference.

This helper never changes source segment IDs or timestamps.  It maps the
reference transcript monotonically onto the existing Chirp subtitle segments
and emits only the preferred handoff contract:

    [{"segment_id": "...", "corrected_text": "..."}, ...]

The downstream production importer remains responsible for enforcing exact
segment coverage/order and for continuing cleanup / Golden Rules / QA.
"""

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any


NORMAL_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]")


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if NORMAL_RE.fullmatch(ch))


def _norm_positions(value: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(value):
        if NORMAL_RE.fullmatch(char):
            chars.append(char.lower())
            positions.append(index)
    return "".join(chars), positions


def _source_text(item: dict[str, Any]) -> str:
    return str(
        item.get("corrected_text")
        or item.get("text")
        or item.get("raw_text")
        or ""
    ).strip()


def _clean_reference(value: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    before = text
    text = re.sub(r"(?m)^\s*【[^\n】]+】\s*$", "", text)
    if text != before:
        notes.append("removed_editorial_bracket_headings")
    cyrillic = len(re.findall(r"[\u0400-\u04ff]", text))
    if cyrillic:
        text = re.sub(r"[\u0400-\u04ff]+", "", text)
        notes.append(f"removed_cyrillic_chars={cyrillic}")
    return text.strip(), notes


def _map_boundaries(source_norm: str, reference_norm: str, bounds: list[int]) -> tuple[list[int], float]:
    matcher = difflib.SequenceMatcher(None, source_norm, reference_norm, autojunk=False)
    anchors: list[tuple[int, int]] = [(0, 0)]
    for block in matcher.get_matching_blocks():
        if block.size:
            anchors.append((int(block.a), int(block.b)))
            anchors.append((int(block.a + block.size), int(block.b + block.size)))
    anchors.append((len(source_norm), len(reference_norm)))

    merged: list[tuple[int, int]] = []
    for source_pos, reference_pos in sorted(anchors):
        if merged and merged[-1][0] == source_pos:
            merged[-1] = (source_pos, max(merged[-1][1], reference_pos))
        else:
            merged.append((source_pos, reference_pos))

    monotonic: list[tuple[int, int]] = []
    previous = 0
    for source_pos, reference_pos in merged:
        reference_pos = max(previous, min(len(reference_norm), reference_pos))
        monotonic.append((source_pos, reference_pos))
        previous = reference_pos

    def project(position: int) -> int:
        if position <= 0:
            return 0
        if position >= len(source_norm):
            return len(reference_norm)
        left = 0
        right = len(monotonic) - 1
        while left + 1 < right:
            middle = (left + right) // 2
            if monotonic[middle][0] <= position:
                left = middle
            else:
                right = middle
        source_left, ref_left = monotonic[left]
        source_right, ref_right = monotonic[right]
        if source_right == source_left:
            return ref_left
        return round(
            ref_left
            + (position - source_left)
            * (ref_right - ref_left)
            / (source_right - source_left)
        )

    mapped = [project(value) for value in bounds]
    mapped[0] = 0
    mapped[-1] = len(reference_norm)
    for index in range(1, len(mapped)):
        mapped[index] = max(mapped[index - 1], mapped[index])
    for index in range(len(mapped) - 2, -1, -1):
        mapped[index] = min(mapped[index], mapped[index + 1])
    return mapped, matcher.ratio()


def build_edits(segments: list[dict[str, Any]], reference: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    source_parts: list[str] = []
    source_bounds = [0]
    for item in segments:
        normalized = _norm(_source_text(item))
        source_parts.append(normalized)
        source_bounds.append(source_bounds[-1] + len(normalized))
    source_norm = "".join(source_parts)
    reference_norm, reference_positions = _norm_positions(reference)
    if not source_norm or not reference_norm:
        raise ValueError("source/reference normalized text is empty")

    mapped, similarity = _map_boundaries(source_norm, reference_norm, source_bounds)

    def raw_boundary(index: int) -> int:
        if index <= 0:
            return 0
        if index >= len(reference_positions):
            return len(reference)
        return reference_positions[index]

    edits: list[dict[str, str]] = []
    fallback_raw_segments = 0
    for index, item in enumerate(segments):
        start = raw_boundary(mapped[index])
        end = raw_boundary(mapped[index + 1])
        corrected = reference[start:end]
        corrected = re.sub(r"\s+", " ", corrected).strip()
        corrected = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", corrected)
        if not corrected:
            corrected = _source_text(item)
            fallback_raw_segments += 1
        if not corrected:
            raise ValueError(f"empty source and reference mapping at segment {index + 1}")
        segment_id = str(item.get("segment_id") or f"seg-{index + 1:04d}")
        edits.append({"segment_id": segment_id, "corrected_text": corrected})

    final_norm = _norm("".join(item["corrected_text"] for item in edits))
    qa = {
        "schema_version": 1,
        "profile": "chatgpt_reference_monotonic_segment_edits",
        "segment_count": len(segments),
        "source_norm_chars": len(source_norm),
        "reference_norm_chars": len(reference_norm),
        "final_norm_chars": len(final_norm),
        "source_vs_reference_similarity": round(similarity, 6),
        "final_vs_reference_similarity": round(
            difflib.SequenceMatcher(None, final_norm, reference_norm, autojunk=False).ratio(),
            6,
        ),
        "final_norm_exact_reference": final_norm == reference_norm,
        "fallback_raw_segments": fallback_raw_segments,
        "timestamps_in_output": False,
    }
    return edits, qa


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.segments.read_text(encoding="utf-8"))
    segments = payload.get("segments", []) if isinstance(payload, dict) else payload
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments input has no segments")
    reference, cleanup_notes = _clean_reference(
        args.reference.read_text(encoding="utf-8-sig", errors="replace")
    )
    edits, qa = build_edits(segments, reference)

    output = {
        "schema_version": 1,
        "segment_edits": edits,
        "qa": {**qa, "reference_cleanup_notes": cleanup_notes},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output["qa"]["sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".report.json").write_text(
        json.dumps(output["qa"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output["qa"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
