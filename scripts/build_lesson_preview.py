"""Build a non-destructive lesson preview with conservative text cleanup.

The raw and Gemini-corrected files are never modified.  The preview removes
only obvious stutters, then replaces the final repeated chant with the supplied
canonical mantra once (the first occurrence is kept).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from app.providers.mantra_context import MANTRA_LINES, MANTRA_TITLE


def normalize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", value)


def clean_fillers(value: str) -> tuple[str, int]:
    original = value
    # Remove only boundary interjections.  Do not globally remove 啊/喔/哦/就是/
    # 那個 because they can be meaningful inside a sentence or quoted teaching.
    value = re.sub(r"我{2,}", "我", value)
    value = re.sub(r"([你他她它這那])\1{1,}", r"\1", value)
    value = re.sub(r"(?:^|[，,、。！？!？\s])(呃|嗯|欸)(?=$|[，,、。！？!？\s])", "", value)
    boundary = r"(?:啊|喔|哦|哎|誒|欸|嘿|嗯)"
    value = re.sub(rf"^(?:{boundary}[，,、\s]*)+", "", value)
    value = re.sub(rf"(?:[，,、\s]*{boundary})+$", "", value)
    value = re.sub(r"[，,、]{2,}", "，", value)
    if value != original and value.startswith(("，", ",", "、")):
        value = value.lstrip("，,、")
    return value, int(value != original)


def timestamp(value: int, separator: str = ",") -> str:
    hours, value = divmod(max(0, value), 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def _canonical_anchors(segments: list[dict[str, Any]], start: int) -> list[dict[str, Any]]:
    expected = [MANTRA_TITLE, *MANTRA_LINES]
    cursor = 0
    anchors: list[dict[str, Any]] = []
    for index in range(start, len(segments)):
        text = str(segments[index].get("corrected_text", ""))
        compact = normalize(text)
        search_from = 0
        while cursor < len(expected):
            target = normalize(expected[cursor])
            position = compact.find(target, search_from)
            if position < 0:
                break
            anchors.append({"expected": expected[cursor], "index": index, "position": position, "length": len(target)})
            cursor += 1
            search_from = position + len(target)
        if cursor == len(expected):
            return anchors
    return []


def _fallback_window(segments: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Locate a tail chant when ASR never produced the canonical spelling."""
    search_start = max(0, len(segments) - 180)
    title_marker = next(
        (
            i
            for i in range(search_start, len(segments))
            if "得見" in normalize(str(segments[i].get("corrected_text", "")))
            and "神咒" in normalize(str(segments[i].get("corrected_text", "")))
        ),
        None,
    )
    if title_marker is not None:
        start = title_marker
    else:
        marker = next(
        (
            i
            for i in range(search_start, len(segments))
            if "合掌" in normalize(str(segments[i].get("corrected_text", "")))
        ),
        None,
        )
        if marker is None:
            return None
        start = marker + 1
    end = next(
        (
            i
            for i in range(start, len(segments))
            if "起立" in normalize(str(segments[i].get("corrected_text", "")))
        ),
        len(segments),
    )
    if start >= end:
        return None
    return start, end


def _evenly_timed_cues(
    segments: list[dict[str, Any]], expected: list[str], end_ms: int | None = None
) -> list[dict[str, Any]]:
    if not segments:
        return []
    start_ms = int(segments[0]["start_ms"])
    end_ms = int(segments[-1]["end_ms"]) if end_ms is None else int(end_ms)
    cues: list[dict[str, Any]] = []
    for index, text in enumerate(expected):
        cue_start = start_ms + round((end_ms - start_ms) * index / len(expected))
        cue_end = start_ms + round((end_ms - start_ms) * (index + 1) / len(expected))
        cue_end = max(cue_start + 1, cue_end)
        source = segments[min(index * len(segments) // len(expected), len(segments) - 1)]
        cues.append(
            {
                **source,
                "segment_id": f"{source['segment_id']}-mantra-{index+1:02}",
                "corrected_text": text,
                "text": text,
                "mantra_dedup_action": "canonical_first_occurrence_fallback_timing",
                "start_ms": cue_start,
                "end_ms": cue_end,
            }
        )
    return cues


def build_preview(source: dict[str, Any]) -> dict[str, Any]:
    segments = source["segments"]
    title_norm = normalize(MANTRA_TITLE)
    mantra_start = next(
        (i for i, item in enumerate(segments) if title_norm in normalize(str(item.get("corrected_text", "")))),
        None,
    )
    fallback_timing = False
    fallback_window = None
    if mantra_start is None:
        fallback_window = _fallback_window(segments)
        if fallback_window is None:
            raise RuntimeError("could not locate mantra tail marker")
        mantra_start = fallback_window[0]
        fallback_timing = True
    anchors = _canonical_anchors(segments, mantra_start)
    expected = [MANTRA_TITLE, *MANTRA_LINES]
    if len(anchors) != len(expected) and not fallback_timing:
        fallback_window = _fallback_window(segments)
        if fallback_window is not None:
            mantra_start = fallback_window[0]
            fallback_timing = True
            anchors = _canonical_anchors(segments, mantra_start)

    # Use the first matching mantra segment's timing, splitting a segment when
    # several canonical lines share it.  This preserves the original audio
    # timing while making the displayed text exact and readable.
    if len(anchors) == len(expected):
        by_index: dict[int, list[dict[str, Any]]] = {}
        for anchor in anchors:
            by_index.setdefault(anchor["index"], []).append(anchor)
        canonical_cues: list[dict[str, Any]] = []
        for index, group in by_index.items():
            segment = segments[index]
            compact_len = max(1, len(normalize(str(segment.get("corrected_text", "")))))
            duration = int(segment["end_ms"]) - int(segment["start_ms"])
            for anchor in group:
                start_ms = int(segment["start_ms"]) + round(duration * anchor["position"] / compact_len)
                end_ms = int(segment["start_ms"]) + round(
                    duration * (anchor["position"] + anchor["length"]) / compact_len
                )
                end_ms = max(start_ms + 1, min(int(segment["end_ms"]), end_ms))
                canonical_cues.append(
                    {
                        **segment,
                        "segment_id": f"{segment['segment_id']}-mantra-{len(canonical_cues)+1:02}",
                        "corrected_text": anchor["expected"],
                        "text": anchor["expected"],
                        "raw_text": segment.get("raw_text", ""),
                        "mantra_dedup_action": "canonical_first_occurrence",
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                    }
                )
        cycle_end = fallback_window[1] if fallback_window else max(a["index"] for a in anchors) + 1
    else:
        if not fallback_timing or fallback_window is None:
            raise RuntimeError(f"could not locate one complete canonical mantra (anchors={len(anchors)})")
        block_end = fallback_window[1]
        first_phrase = next(
            (
                normalize(str(segments[i].get("corrected_text", "")))
                for i in range(mantra_start, block_end)
                if normalize(str(segments[i].get("corrected_text", "")))
                and "得見" not in str(segments[i].get("corrected_text", ""))
                and "大明神咒" not in str(segments[i].get("corrected_text", ""))
            ),
            "",
        )
        repeated_start = next(
            (
                i
                # The first two matches are usually the leader and the
                # congregation echo; skip them before locating the next cycle.
                for i in range(mantra_start + 10, block_end)
                if first_phrase and normalize(str(segments[i].get("corrected_text", ""))) == first_phrase
            ),
            block_end,
        )
        if repeated_start < block_end:
            cycle_end = repeated_start
            canonical_cues = _evenly_timed_cues(segments[mantra_start:cycle_end], expected)
        else:
            first_segment_text = next(
                (
                    normalize(str(segments[i].get("corrected_text", "")))
                    for i in range(mantra_start + 1, block_end)
                    if normalize(str(segments[i].get("corrected_text", "")))
                    and "得見" not in str(segments[i].get("corrected_text", ""))
                    and "大明神咒" not in str(segments[i].get("corrected_text", ""))
                ),
                normalize(str(segments[mantra_start].get("corrected_text", ""))),
            )
            phrase = first_segment_text[: min(12, len(first_segment_text))]
            joined = "".join(
                normalize(str(segments[i].get("corrected_text", "")))
                for i in range(mantra_start, block_end)
            )
            block_start_ms = int(segments[mantra_start]["start_ms"])
            block_end_ms = int(segments[block_end - 1]["end_ms"])
            # A complete leader/congregation chant is roughly one minute.
            # Duration is more reliable than malformed phonetic ASR text when
            # a segment contains several repeated lines.
            cycles = max(1, round((block_end_ms - block_start_ms) / 58_000))
            first_cycle_end_ms = block_start_ms + round((block_end_ms - block_start_ms) / cycles)
            cycle_end = next(
                (i for i in range(mantra_start, block_end) if int(segments[i]["end_ms"]) >= first_cycle_end_ms),
                block_end,
            )
            canonical_cues = _evenly_timed_cues(
                segments[mantra_start:cycle_end], expected, first_cycle_end_ms
            )

    # Suppress every mantra-like segment after the title through the end of the
    # chant.  The next ordinary segment (大眾請起立) is retained unchanged.
    canonical_norms = [normalize(value) for value in (MANTRA_LINES + (MANTRA_TITLE,))]
    end = fallback_window[1] if fallback_window else mantra_start
    if not fallback_window:
        while end < len(segments):
            compact = normalize(str(segments[end].get("corrected_text", "")))
            if not compact or not any(token and token in compact for token in canonical_norms):
                break
            end += 1

    output_segments: list[dict[str, Any]] = []
    filler_changed = 0
    empty_suppressed = 0
    for index, segment in enumerate(segments):
        if index == mantra_start:
            output_segments.extend(canonical_cues)
        if mantra_start <= index < end:
            continue
        cleaned, changed = clean_fillers(str(segment.get("corrected_text", "")))
        if changed and not cleaned.strip():
            empty_suppressed += 1
            continue
        copy = {**segment, "corrected_text": cleaned, "text": cleaned}
        if changed:
            copy["filler_cleanup"] = "conservative_stutter_only"
            filler_changed += 1
        output_segments.append(copy)

    return {
        **source,
        "source": f"{source.get('source', 'corrected preview')} + deterministic preview cleanup",
        "preview_only": True,
        "mantra_dedup": {
            "title": MANTRA_TITLE,
            "canonical_line_count": len(MANTRA_LINES),
            "kept_occurrence": "first",
            "suppressed_source_segments": end - mantra_start,
            "canonical_cue_count": len(canonical_cues),
            "source_start_segment": segments[mantra_start]["segment_id"],
            "source_end_segment_exclusive": segments[end]["segment_id"] if end < len(segments) else None,
            "timing_mode": "fallback_evenly_distributed" if fallback_timing else "matched_segment_text",
        },
        "filler_cleanup": {
            "rule": "boundary interjections plus unambiguous stutters; inner 啊/喔/哦/就是/那個 retained",
            "changed_segment_count": filler_changed,
            "empty_segment_suppressed": empty_suppressed,
        },
        "segment_count": len(output_segments),
        "segments": output_segments,
    }


def write_exports(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "subtitles-preview-dedup.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    segments = payload["segments"]
    (output_dir / "subtitles-preview-dedup.srt").write_text(
        "\n\n".join(
            f"{index}\n{timestamp(int(item['start_ms']))} --> {timestamp(int(item['end_ms']))}\n{item['corrected_text']}"
            for index, item in enumerate(segments, 1)
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "transcript-preview-dedup.txt").write_text(
        "\n".join(str(item["corrected_text"]) for item in segments) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    payload = build_preview(json.loads(args.input.read_text(encoding="utf-8")))
    write_exports(payload, args.output_dir)
    print(
        f"PREVIEW=PASS segments={payload['segment_count']} "
        f"filler_changed={payload['filler_cleanup']['changed_segment_count']} "
        f"mantra_cues={payload['mantra_dedup']['canonical_cue_count']} "
        f"suppressed={payload['mantra_dedup']['suppressed_source_segments']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
