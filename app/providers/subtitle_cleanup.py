"""Conservative, deterministic subtitle cleanup and review detection.

This stage never changes segment IDs or timestamps and never overwrites the
raw Chirp or Gemini evidence.  It only derives a cleaned text layer for user
facing exports and records every automatic change plus items that need review.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from app.providers.mantra_context import MANTRA_LINES, MANTRA_TITLE
except ImportError:  # keep local cleanup/QA usable without optional Chirp SDK
    MANTRA_TITLE = "《得見彌勒根本大明神咒》"
    MANTRA_LINES = (
        "南謨囉怛那怛囉夜耶。", "南謨吠嚕左那莎彌儞。", "怛他誐多耶。",
        "阿囉喝帝三藐三沒馱耶。", "怛姪他。唵。", "昧咄侶怛哩。",
        "昧怛囉縛婆悉儞。", "昧咄侶怛葛吒耶。", "三摩囉三摩囉。",
        "莎剛鉢囉底倪也。", "娑囉娑囉。", "尾娑囉尾娑囉。",
        "冒馱耶。冒馱耶。", "冒馱耨誐帝。", "摩訶冒地。", "波哩縛哩",
        "底多摩那細 莎訶。",
    )

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
FILLERS = "嗯呃欸誒啊喔哦哎嘿"
BOUNDARY_FILLER_RE = re.compile(rf"^(?:[{FILLERS}][\s，、。！？,.!?]*){{1,4}}")
BOUNDARY_FILLER_TAIL_RE = re.compile(rf"(?:[\s，、。！？,.!?]*[{FILLERS}]){{1,4}}$")
TRIPLE_STUTTER_RE = re.compile(r"([\u4e00-\u9fffA-Za-z]{1,3})\1{2,}")
DOUBLE_STUTTER_RE = re.compile(r"([\u4e00-\u9fffA-Za-z]{1,2})\1")
INTERRUPTION_RE = re.compile(r"(?:音訊|音頻|聲音).{0,4}(?:中斷|斷線|消失)|\[+\s*(?:inaudible|不清楚|聽不清)\s*\]+", re.I)


def _mantra_key(value: str) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", str(value or "")).lower()


def _matches_mantra_line(item: dict[str, Any], line: str) -> bool:
    """Use a strict match: a false suppression is worse than a duplicate."""
    actual = _mantra_key(item.get("cleaned_text", ""))
    expected = _mantra_key(line)
    return bool(actual and expected and (actual == expected or expected in actual))


def _find_full_mantra_cycle(items: list[dict[str, Any]], start: int) -> int | None:
    end = start + len(MANTRA_LINES)
    if end > len(items):
        return None
    if all(_matches_mantra_line(item, line) for item, line in zip(items[start:end], MANTRA_LINES)):
        return end
    return None


def _mantra_display_layer(cleaned: list[dict[str, Any]], content_mode: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build a separate publication layer for a verified two-cycle mantra.

    The raw/corrected/cleaned segment layers remain one-for-one with Chirp.
    Only the display layer omits the second *complete, contiguous and ordered*
    recitation.  It never emits empty cues or overwrites intervening speech.
    """
    display = [{**item, "source_segment_ids": [item["segment_id"]]} for item in cleaned]
    if content_mode != "dacheng_buddhist":
        return display, {"applied": False, "reason": "content_mode_not_buddhist"}
    if len(cleaned) < len(MANTRA_LINES) * 2:
        return display, {"applied": False, "reason": "insufficient_segments"}
    # Two complete, exact cycles are stronger evidence than an arbitrary
    # position threshold. Do not reject short lessons where the closing chant
    # occupies most of the recording.
    start_floor = 0
    for first in range(len(cleaned) - len(MANTRA_LINES) * 2 + 1):
        if int(cleaned[first].get("start_ms", 0)) < start_floor:
            continue
        first_end = _find_full_mantra_cycle(cleaned, first)
        if first_end is None:
            continue
        second_end = _find_full_mantra_cycle(cleaned, first_end)
        if second_end is None:
            continue
        # Canonicalise the first cycle only in display output. Combining the
        # title with the first line avoids creating an artificial cue/timing.
        for offset, line in enumerate(MANTRA_LINES):
            cue = display[first + offset]
            cue["cleaned_text"] = f"{MANTRA_TITLE}\n{line}" if offset == 0 else line
            cue["corrected_text"] = cue["cleaned_text"]
            cue["cleanup_actions"] = [*cue.get("cleanup_actions", []), "mantra_display_canonicalized"]
        display = [*display[:first_end], *display[second_end:]]
        return display, {
            "applied": True,
            "first_cycle_start_segment_id": cleaned[first]["segment_id"],
            "first_cycle_end_segment_id": cleaned[first_end - 1]["segment_id"],
            "suppressed_cycle_start_segment_id": cleaned[first_end]["segment_id"],
            "suppressed_cycle_end_segment_id": cleaned[second_end - 1]["segment_id"],
            "suppressed_cues": len(MANTRA_LINES),
            "canonical_line_count": len(MANTRA_LINES),
            "publication_layer": "display_segments",
        }
    return display, {"applied": False, "reason": "no_complete_contiguous_two_cycle_match"}


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _timestamp(value: int, separator: str = ",") -> str:
    value = max(0, int(value))
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def clean_text(value: str) -> tuple[str, list[str]]:
    """Remove only high-confidence boundary fillers and triple stutters."""
    text = str(value or "").strip()
    actions: list[str] = []
    without_prefix = BOUNDARY_FILLER_RE.sub("", text, count=1).strip()
    if without_prefix != text:
        actions.append("boundary_filler_prefix")
    text = without_prefix
    without_suffix = BOUNDARY_FILLER_TAIL_RE.sub("", text, count=1).strip()
    if without_suffix != text:
        actions.append("boundary_filler_suffix")
    text = without_suffix
    collapsed = TRIPLE_STUTTER_RE.sub(r"\1", text)
    if collapsed != text:
        actions.append("triple_stutter")
    return collapsed.strip(), actions


def _base_segments() -> tuple[str, list[dict[str, Any]]]:
    for name in ("subtitles-corrected.json", "subtitles.json"):
        path = JOB / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            continue
        result: list[dict[str, Any]] = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            raw = str(item.get("raw_text", item.get("text", "")))
            corrected = str(item.get("corrected_text", item.get("text", raw)))
            result.append(
                {
                    **item,
                    "segment_id": str(item.get("segment_id", f"seg-{index:04d}")),
                    "start_ms": int(item.get("start_ms", 0)),
                    "end_ms": int(item.get("end_ms", 0)),
                    "raw_text": raw,
                    "corrected_text": corrected,
                }
            )
        return name, result
    raise RuntimeError("subtitles.json or subtitles-corrected.json is missing")


def build_report(
    source_name: str,
    segments: list[dict[str, Any]],
    *,
    content_mode: str | None = None,
) -> dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    changed_count = 0
    for index, item in enumerate(segments, 1):
        source_text = str(item["corrected_text"] or item["raw_text"])
        cleaned_text, actions = clean_text(source_text)
        reasons: list[str] = []
        duration = max(0, int(item["end_ms"]) - int(item["start_ms"]))
        if not cleaned_text:
            cleaned_text = source_text.strip() or str(item["raw_text"]).strip()
            reasons.append("cleanup_would_make_segment_empty")
        if DOUBLE_STUTTER_RE.search(cleaned_text) and not TRIPLE_STUTTER_RE.search(cleaned_text):
            reasons.append("possible_double_stutter")
        if FILLERS and re.search(rf"[{FILLERS}]", cleaned_text[1:-1] if len(cleaned_text) > 2 else ""):
            reasons.append("inner_filler_review")
        if INTERRUPTION_RE.search(cleaned_text):
            reasons.append("possible_audio_interruption")
        if len(cleaned_text) > int(os.environ.get("CLEANUP_MAX_CHARS_PER_CUE", "48")):
            reasons.append("long_cue_review")
        if int(item["end_ms"]) <= int(item["start_ms"]):
            reasons.append("invalid_timing")
        if actions:
            changed_count += 1
        cleaned_item = {
            **item,
            "cleaned_text": cleaned_text,
            "corrected_text": cleaned_text,
            "cleanup_actions": actions,
            "cleanup_review_reasons": reasons,
            "char_count": len(cleaned_text),
            "duration_ms": duration,
            "chars_per_second": round(len(cleaned_text) / (duration / 1000), 2) if duration else None,
        }
        cleaned.append(cleaned_item)
        if reasons:
            review.append({
                "segment_id": cleaned_item["segment_id"],
                "start_ms": cleaned_item["start_ms"],
                "end_ms": cleaned_item["end_ms"],
                "text": cleaned_text,
                "reasons": reasons,
            })

    duplicate_count = 0
    for previous, current in zip(cleaned, cleaned[1:]):
        if previous["cleaned_text"] and previous["cleaned_text"] == current["cleaned_text"]:
            duplicate_count += 1
            current["cleanup_review_reasons"].append("possible_duplicate_cue")
            review.append({
                "segment_id": current["segment_id"],
                "start_ms": current["start_ms"],
                "end_ms": current["end_ms"],
                "text": current["cleaned_text"],
                "reasons": ["possible_duplicate_cue"],
            })

    display_segments, mantra = _mantra_display_layer(
        cleaned,
        content_mode or os.environ.get("CONTENT_MODE", "legacy_unspecified").strip().lower(),
    )

    return {
        "version": "cleanup-v2-display-layer",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source_name,
        "timestamps_immutable": True,
        "status": "REVIEW" if review else "PASS",
        "summary": {
            "segment_count": len(cleaned),
            "changed_count": changed_count,
            "review_count": len(review),
            "possible_duplicate_cue_count": duplicate_count,
            "total_cleaned_chars": sum(len(str(item["cleaned_text"])) for item in cleaned),
            "mantra": mantra,
        },
        "segments": cleaned,
        "display_segments": display_segments,
        "review_required": review,
        "mantra": mantra,
    }


def main() -> int:
    source_name, segments = _base_segments()
    report = build_report(source_name, segments)
    _atomic_json(
        JOB / "subtitles-cleaned.json",
        {
            "source": source_name,
            "segments": report["segments"],
            "display_segments": report["display_segments"],
            "display_layer": "display_segments",
        },
    )
    cleaned = report["display_segments"]
    _atomic_text(
        JOB / "subtitles-cleaned.srt",
        "\n\n".join(
            f"{index}\n{_timestamp(item['start_ms'])} --> {_timestamp(item['end_ms'])}\n{item['cleaned_text']}"
            for index, item in enumerate(cleaned, 1)
        )
        + "\n",
    )
    _atomic_text(
        JOB / "subtitles-cleaned.vtt",
        "WEBVTT\n\n"
        + "\n\n".join(
            f"{_timestamp(item['start_ms'], '.')} --> {_timestamp(item['end_ms'], '.')}\n{item['cleaned_text']}"
            for item in cleaned
        )
        + "\n",
    )
    _atomic_text(JOB / "transcript-cleaned.txt", "\n".join(item["cleaned_text"] for item in cleaned) + "\n")
    _atomic_json(JOB / "cleanup-review.json", {k: v for k, v in report.items() if k != "segments"})
    summary = report["summary"]
    print(
        f"CLEANUP={report['status']} segments={summary['segment_count']} "
        f"changed={summary['changed_count']} review={summary['review_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
