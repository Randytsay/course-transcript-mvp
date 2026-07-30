"""Correct SRT segment text via Gemini 3.6 Flash without changing timestamps.

Strategy:
- Read existing subtitles.json (output of build_srt.py)
- Group consecutive segments into ~30s windows
- Send each window's segment list to Gemini with a strict schema
- Gemini returns corrected text per segment (same count, same order)
- Preserve every original timestamp; only replace text
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path("/app")
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = ROOT / "data" / "jobs" / JOB_NAME
WORK = JOB / "correct-work"

WINDOW_TARGET_MS = 30_000
MAX_WORKERS = 3


SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "corrected_text": {"type": "string"},
                },
                "required": ["index", "corrected_text"],
            },
        },
    },
    "required": ["segments"],
}


def call_gemini(client, model: str, items: list[dict]):
    """Single Gemini call. Returns (corrected_segments, usage_metadata)."""
    prompt = (
        "You are correcting an ASR transcript (Traditional-Chinese course) produced by Chirp 3. "
        "Rules: (1) Convert Simplified Chinese to Traditional Chinese; "
        "(2) Fix only clear ASR errors (homophones, word segmentation); "
        "(3) Preserve English names, technical terms, and acronyms verbatim; "
        "(4) Preserve speaker wording—do NOT summarize, shorten, or add content; "
        "(5) Return JSON only, one entry per input segment, in the same order, "
        "with the SAME count of segments. Output empty string only if the segment is pure noise.\n\n"
        f"Segments: {json.dumps(items, ensure_ascii=False)}"
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0,
        ),
    )
    answer = json.loads(response.text)
    return answer.get("segments", []), response.usage_metadata


def correct_window(model: str, segments: list[dict]) -> list[dict]:
    """Send one window of segments to Gemini, return list of {index, corrected_text}.

    Tolerant: if Gemini returns fewer segments than input, the original text
    is preserved for the missing ones.
    """
    index = segments[0]["segment_index"]
    work_path = WORK / f"window-{index:04d}.json"
    if work_path.exists():
        cached = json.loads(work_path.read_text(encoding="utf-8"))
        return cached["segments"]

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    client = genai.Client(vertexai=True, project=project, location=location)

    items = [
        {"index": s["segment_index"], "text": s["text"]}
        for s in segments
    ]

    corrected, usage = call_gemini(client, model, items)
    if len(corrected) != len(segments):
        # Retry once with stronger emphasis on count
        retry_items = [
            {**it, "required_count": len(items)}
            for it in items
        ]
        corrected, usage = call_gemini(client, model, retry_items)
    if len(corrected) != len(segments):
        # Final fallback: use original text for missing ones
        print(
            f"  warn: window@{index} gemini returned {len(corrected)} of {len(segments)}; "
            f"filling missing with original text",
            flush=True,
        )
        by_idx = {seg.get("index"): seg.get("corrected_text", "") for seg in corrected}
        corrected = [
            {"index": s["segment_index"], "corrected_text": by_idx.get(s["segment_index"], s["text"])}
            for s in segments
        ]
    work_path.write_text(
        json.dumps(
            {
                "source_start_ms": segments[0]["start_ms"],
                "source_end_ms": segments[-1]["end_ms"],
                "usage_metadata": usage.model_dump(mode="json") if usage else None,
                "segments": corrected,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"CORRECT window segments[{segments[0]['segment_index']}-{segments[-1]['segment_index']}]=PASS", flush=True)
    return corrected


def build_windows(segments: list[dict]) -> list[list[dict]]:
    windows: list[list[dict]] = []
    current: list[dict] = []
    current_start: int | None = None

    for seg in segments:
        if current_start is None:
            current_start = seg["start_ms"]
            current.append(seg)
            continue

        if seg["end_ms"] - current_start >= WINDOW_TARGET_MS and current:
            windows.append(current)
            current = [seg]
            current_start = seg["start_ms"]
        else:
            current.append(seg)

    if current:
        windows.append(current)
    return windows


def main() -> int:
    sub_path = JOB / "subtitles.json"
    if not sub_path.exists():
        print("CORRECT=FAIL subtitles.json not found, run build_srt first")
        return 1

    sub = json.loads(sub_path.read_text(encoding="utf-8"))
    raw_segments = sub["segments"]

    indexed = [
        {"segment_index": i, **s}
        for i, s in enumerate(raw_segments)
    ]
    windows = build_windows(indexed)
    print(f"CORRECT: {len(indexed)} segments in {len(windows)} windows")

    model = os.environ.get("PHASE2_MODEL", "gemini-3.6-flash")
    if model != "gemini-3.6-flash":
        print(f"CORRECT=FAIL model {model} is not the approved gemini-3.6-flash")
        return 1

    WORK.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda w: correct_window(model, w), windows))
    print(f"CORRECT: all windows done in {time.monotonic()-t0:.0f}s")

    # Stitch corrected text back onto the original segments
    corrected_by_idx: dict[int, str] = {}
    for window_result in results:
        for entry in window_result:
            corrected_by_idx[entry["index"]] = entry["corrected_text"]

    final_segments = []
    for raw in raw_segments:
        idx = raw_segments.index(raw)
        new_text = corrected_by_idx.get(idx, raw["text"])
        final_segments.append({**raw, "text": new_text, "corrected": new_text != raw["text"]})

    out_json = JOB / "subtitles-corrected.json"
    out_json.write_text(
        json.dumps(
            {
                "source": "chirp_3_merged + gemini-3.6-flash text correction",
                "segment_count": len(final_segments),
                "corrected_count": sum(1 for s in final_segments if s["corrected"]),
                "total_duration_ms": final_segments[-1]["end_ms"] if final_segments else 0,
                "segments": final_segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Re-emit SRT
    def srt_time(ms: int) -> str:
        ms = max(0, ms)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1_000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    cues = [
        f"{i}\n{srt_time(s['start_ms'])} --> {srt_time(s['end_ms'])}\n{s['text']}"
        for i, s in enumerate(final_segments, 1)
    ]
    (JOB / "subtitles-corrected.srt").write_text("\n\n".join(cues) + "\n", encoding="utf-8")

    print(
        f"CORRECT=PASS segments={len(final_segments)} "
        f"corrected={sum(1 for s in final_segments if s['corrected'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())