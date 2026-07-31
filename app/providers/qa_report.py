"""Strict structural QA. A report is PASS only when publication is safe."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")


def atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def duration_ms() -> int:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(JOB / "normalized.flac")], capture_output=True, text=True, check=True)
    return round(float(result.stdout.strip()) * 1000)


def main() -> int:
    merged = json.loads((JOB / "merged-words.json").read_text(encoding="utf-8"))
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    corrected_path = JOB / "subtitles-corrected.json"
    corrected = json.loads(corrected_path.read_text(encoding="utf-8")) if corrected_path.exists() else None
    words, segments = merged["words"], raw["segments"]
    errors, warnings = [], []
    if any(int(word["end_ms"]) <= int(word["start_ms"]) for word in words): errors.append("merged words contain non-positive durations")
    if any(next_word["start_ms"] < word["start_ms"] for word, next_word in zip(words, words[1:])): errors.append("merged word starts regress")
    ids = [item.get("segment_id") for item in segments]
    if len(ids) != len(set(ids)) or any(not value for value in ids): errors.append("segment IDs are not unique")
    invalid = [item["segment_id"] for item in segments if int(item["end_ms"]) <= int(item["start_ms"])]
    if invalid: errors.append(f"non-positive subtitle durations: {invalid[:10]}")
    overlaps = [{"before": a["segment_id"], "after": b["segment_id"], "ms": int(a["end_ms"]) - int(b["start_ms"])} for a, b in zip(segments, segments[1:]) if int(b["start_ms"]) < int(a["end_ms"])]
    if overlaps: errors.append(f"subtitle overlaps: {len(overlaps)}")
    long_gaps = [{"before": a["segment_id"], "after": b["segment_id"], "gap_ms": int(b["start_ms"]) - int(a["end_ms"])} for a, b in zip(segments, segments[1:]) if int(b["start_ms"]) - int(a["end_ms"]) > 5000]
    if long_gaps: warnings.append(f"subtitle gaps over 5 seconds: {len(long_gaps)}")
    audio = duration_ms(); end = int(segments[-1]["end_ms"]) if segments else 0; uncovered = max(0, audio - end)
    if uncovered > 1000: errors.append(f"audio tail uncovered: {uncovered}ms")
    correction_invariant = None
    if corrected:
        correction_invariant = len(corrected["segments"]) == len(segments) and all((a["segment_id"], a["start_ms"], a["end_ms"]) == (b["segment_id"], b["start_ms"], b["end_ms"]) for a, b in zip(segments, corrected["segments"]))
        if not correction_invariant: warnings.append("existing correction is stale and requires a new segment-level correction pass")
    report = {"generated_at": datetime.now(UTC).isoformat(), "job": JOB.name, "status": "PASS" if not errors else "FAIL", "errors": errors, "warnings": warnings, "audio": {"duration_ms": audio}, "chirp": {"model": "chirp_3", "word_count": len(words), "timeline_end_ms": merged.get("total_duration_ms"), "dropped_anomaly_count": merged.get("dropped_anomaly_count", 0)}, "subtitles": {"segment_count": len(segments), "end_ms": end, "uncovered_tail_ms": uncovered, "overlaps": overlaps, "long_gaps": long_gaps}, "correction": {"model": "gemini-3.6-flash" if corrected else None, "immutable_structure_preserved": correction_invariant}}
    atomic(JOB / "qa-report.json", report)
    md = [f"# QA Report: {JOB.name}", "", f"Status: **{report['status']}**", "", "## Errors"] + ([f"- {item}" for item in errors] or ["- None"]) + ["", "## Warnings"] + ([f"- {item}" for item in warnings] or ["- None"])
    temporary = JOB / "qa-report.md.tmp"; temporary.write_text("\n".join(md) + "\n", encoding="utf-8"); temporary.replace(JOB / "qa-report.md")
    print(f"QA={report['status']} errors={len(errors)} warnings={len(warnings)}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
