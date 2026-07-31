"""Validate all local, publication-ready artifacts without changing them."""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
SRT_TIMING = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
VTT_TIMING = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")


def blocks(path: Path) -> list[list[str]]:
    return [block.splitlines() for block in path.read_text(encoding="utf-8").strip().split("\n\n") if block.strip()]


def main() -> int:
    errors: list[str] = []
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    corrected = json.loads((JOB / "subtitles-corrected.json").read_text(encoding="utf-8"))
    raw_segments, corrected_segments = raw.get("segments", []), corrected.get("segments", [])
    if not raw_segments:
        errors.append("raw subtitle segments are empty")
    if len(raw_segments) != len(corrected_segments):
        errors.append("corrected segment count differs from raw")
    elif any((a["segment_id"], a["start_ms"], a["end_ms"]) != (b["segment_id"], b["start_ms"], b["end_ms"]) for a, b in zip(raw_segments, corrected_segments)):
        errors.append("corrected segment IDs or timing changed")

    for name, timing, indexed in (("subtitles.srt", SRT_TIMING, True), ("subtitles-corrected.srt", SRT_TIMING, True), ("subtitles.vtt", VTT_TIMING, False), ("subtitles-corrected.vtt", VTT_TIMING, False)):
        content = (JOB / name).read_text(encoding="utf-8")
        if not indexed and not content.startswith("WEBVTT\n"):
            errors.append(f"{name} missing WEBVTT header")
        units = blocks(JOB / name)
        if indexed:
            valid = len(units) == len(raw_segments) and all(len(unit) >= 3 and unit[0].isdigit() and timing.match(unit[1]) for unit in units)
        else:
            if units and units[0] == ["WEBVTT"]:
                units = units[1:]
            valid = len(units) == len(raw_segments) and all(len(unit) >= 2 and timing.match(unit[0]) for unit in units)
        if not valid:
            errors.append(f"{name} cue structure mismatch")

    for name in ("subtitles.ass", "subtitles-corrected.ass"):
        dialogues = [line for line in (JOB / name).read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue: ")]
        if len(dialogues) != len(raw_segments):
            errors.append(f"{name} dialogue count mismatch")

    with (JOB / "transcript-segments.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(raw_segments):
        errors.append("transcript-segments.csv row count mismatch")

    manifests = sorted((JOB / "chunks").glob("chunk-*/manifest.json"))
    if len(manifests) < 5:
        errors.append("missing required chunk manifests")
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunk = manifest_path.parent
        if manifest.get("status") != "SUCCEEDED" or not (chunk / "chirp-raw.json").is_file() or not (chunk / "words.json").is_file():
            errors.append(f"incomplete raw Chirp evidence for {chunk.name}")

    correction_records = list((JOB / "correction-v2").glob("*.json"))
    if not correction_records:
        errors.append("missing Gemini correction records")
    for path in correction_records:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("model") != "gemini-3.6-flash" or not isinstance(record.get("raw_response"), str):
            errors.append(f"invalid Gemini evidence: {path.name}")
            break
    if not (JOB / "glossary" / "global-terms.json").is_file() or not (JOB / "join-qa.json").is_file():
        errors.append("missing glossary or join QA")

    print(f"OUTPUT_VALIDATION={'PASS' if not errors else 'FAIL'} errors={len(errors)} segments={len(raw_segments)} correction_records={len(correction_records)}")
    for error in errors:
        print(f"- {error}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
