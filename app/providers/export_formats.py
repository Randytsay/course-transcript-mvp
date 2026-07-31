"""Create deterministic local-only exports from immutable subtitle segments."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def ass_time(value: int) -> str:
    hours, value = divmod(max(0, value), 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours}:{minutes:02}:{seconds:02}.{milliseconds // 10:02}"


def ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def main() -> int:
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    corrected_path = JOB / "subtitles-corrected.json"
    corrected = json.loads(corrected_path.read_text(encoding="utf-8")) if corrected_path.exists() else None
    raw_segments = raw["segments"]
    corrected_by_id = {item["segment_id"]: item for item in corrected["segments"]} if corrected else {}

    header = """[Script Info]
Title: Course Transcript MVP export
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,80,80,60,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    for filename, use_corrected in (("subtitles.ass", False), ("subtitles-corrected.ass", True)):
        lines = [header.rstrip("\n")]
        for segment in raw_segments:
            text = corrected_by_id.get(segment["segment_id"], {}).get("corrected_text", segment["raw_text"]) if use_corrected else segment["raw_text"]
            lines.append(f"Dialogue: 0,{ass_time(int(segment['start_ms']))},{ass_time(int(segment['end_ms']))},Default,,0,0,0,,{ass_text(text)}")
        atomic_text(JOB / filename, "\n".join(lines) + "\n")

    with (JOB / "transcript-segments.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["segment_id", "start_ms", "end_ms", "raw_text", "corrected_text", "uncertain_terms"])
        writer.writeheader()
        for segment in raw_segments:
            item = corrected_by_id.get(segment["segment_id"], {})
            writer.writerow({"segment_id": segment["segment_id"], "start_ms": segment["start_ms"], "end_ms": segment["end_ms"], "raw_text": segment["raw_text"], "corrected_text": item.get("corrected_text", segment["raw_text"]), "uncertain_terms": " | ".join(item.get("uncertain_terms", []))})

    manifest = {
        "scope": "local_only",
        "source_timing": "chirp_3 word timestamps",
        "correction_model": corrected.get("model") if corrected else None,
        "formats": {
            "subtitles.srt": "raw timed subtitles",
            "subtitles.vtt": "raw timed web subtitles",
            "subtitles.ass": "raw timed Advanced SubStation Alpha subtitles",
            "subtitles-corrected.srt": "corrected text on immutable raw timing",
            "subtitles-corrected.vtt": "corrected web subtitles on immutable raw timing",
            "subtitles-corrected.ass": "corrected Advanced SubStation Alpha subtitles on immutable raw timing",
            "subtitles.json": "raw structured segments",
            "subtitles-corrected.json": "corrected structured segments",
            "transcript-*.txt": "plain-text transcripts",
            "transcript-*.md": "timestamped Markdown transcripts",
            "transcript-segments.csv": "per-segment interchange data",
            "glossary/global-terms.csv": "global terminology candidates",
            "qa-report.*": "publication QA evidence",
        },
        "not_generated": ["Google Docs", "DOCX", "PDF"],
        "reason_not_generated": "Drive/Docs OAuth and explicit upload approval are intentionally absent in this local-review stage.",
    }
    atomic_text(JOB / "export-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"EXPORT=PASS segments={len(raw_segments)} corrected={bool(corrected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
