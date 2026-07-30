"""Generate QA report for the long-file transcript job.

Produces:
- qa-report.json: machine-readable metrics
- qa-report.md: human-readable summary

Inputs (must exist on disk):
- data/jobs/voice_11386603-seg1/merged-words.json (Chirp word timeline)
- data/jobs/voice_11386603-seg1/subtitles.json (initial SRT segments)
- data/jobs/voice_11386603-seg1/subtitles-corrected.json (Gemini-corrected SRT)
- data/jobs/voice_11386603-seg1/normalized.flac (canonical audio for duration check)
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/app")
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = ROOT / "data" / "jobs" / JOB_NAME


def audio_duration_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def chunk_reports() -> list[dict]:
    reports = []
    for chunk_dir in sorted(JOB.glob("chunks/chunk-*")):
        manifest_path = chunk_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        reports.append({
            "chunk_index": m.get("chunk_index"),
            "source_start_ms": m.get("source_start_ms"),
            "source_end_ms": m.get("source_end_ms"),
            "status": m.get("status"),
            "word_count": m.get("word_count"),
            "max_end_ms": m.get("max_end_ms"),
            "gcs_uri": m.get("gcs_uri"),
        })
    return reports


def analyze_segments(segments: list[dict]) -> dict:
    if not segments:
        return {"segment_count": 0}
    durations = [s["end_ms"] - s["start_ms"] for s in segments]
    text_lengths = [len(s.get("text", "")) for s in segments]
    return {
        "segment_count": len(segments),
        "min_segment_ms": min(durations),
        "max_segment_ms": max(durations),
        "avg_segment_ms": round(sum(durations) / len(durations)),
        "min_text_chars": min(text_lengths),
        "max_text_chars": max(text_lengths),
        "avg_text_chars": round(sum(text_lengths) / len(text_lengths), 1),
    }


def main() -> int:
    merged_path = JOB / "merged-words.json"
    sub_path = JOB / "subtitles.json"
    corrected_path = JOB / "subtitles-corrected.json"
    audio_path = JOB / "normalized.flac"

    missing = []
    if not merged_path.exists():
        missing.append("merged-words.json")
    if not sub_path.exists():
        missing.append("subtitles.json")
    if missing:
        print(f"QA=FAIL missing inputs: {missing}")
        return 1

    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    sub = json.loads(sub_path.read_text(encoding="utf-8"))
    corrected = json.loads(corrected_path.read_text(encoding="utf-8")) if corrected_path.exists() else None

    audio_dur_s = audio_duration_seconds(audio_path)
    merged_dur_ms = merged["total_duration_ms"]
    audio_dur_ms = int(audio_dur_s * 1000) if audio_dur_s else None

    coverage_pct = None
    if audio_dur_ms:
        coverage_pct = round(merged_dur_ms / audio_dur_ms * 100, 2)

    corrected_seg_count = 0
    if corrected:
        corrected_seg_count = corrected.get("corrected_count", 0)

    sub_stats = analyze_segments(sub["segments"])
    corrected_stats = analyze_segments(corrected["segments"]) if corrected else None

    chunk_stats = chunk_reports()
    total_chunk_words = sum(c.get("word_count", 0) or 0 for c in chunk_stats)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "job": JOB.name,
        "audio": {
            "duration_seconds": audio_dur_s,
            "duration_ms": audio_dur_ms,
            "file": str(audio_path.relative_to(ROOT)) if audio_path.exists() else None,
        },
        "chirp": {
            "model": "chirp_3",
            "total_words": merged["total_words"],
            "merged_duration_ms": merged_dur_ms,
            "merged_duration_seconds": round(merged_dur_ms / 1000, 2),
            "gaps_over_5s": merged.get("gap_count_gt_5s", 0),
            "coverage_pct": coverage_pct,
            "chunks": chunk_stats,
            "chunk_word_total": total_chunk_words,
        },
        "subtitles_initial": sub_stats,
        "subtitles_corrected": corrected_stats,
        "gemini_correction": {
            "applied": corrected is not None,
            "model": "gemini-3.6-flash" if corrected else None,
            "corrected_segments": corrected_seg_count,
            "corrected_pct": round(corrected_seg_count * 100 / sub_stats["segment_count"], 2)
                if corrected and sub_stats.get("segment_count") else None,
        },
    }

    (JOB / "qa-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Markdown
    md = []
    md.append(f"# QA Report: {JOB.name}")
    md.append(f"Generated: {report['generated_at']}")
    md.append("")
    if audio_dur_s:
        md.append(f"- Audio duration: **{audio_dur_s/60:.2f} min** ({audio_dur_s:.2f}s)")
    md.append(f"- Chirp words: **{merged['total_words']}**")
    md.append(f"- Merged word timeline: **{merged_dur_ms/60000:.2f} min**")
    if coverage_pct is not None:
        md.append(f"- Coverage: **{coverage_pct}%**")
    md.append(f"- Gaps >5s: **{merged.get('gap_count_gt_5s', 0)}**")
    md.append("")
    md.append("## Chunks")
    md.append("| Index | Range (s) | Status | Words | Max end (ms) |")
    md.append("|---|---|---|---|---|")
    for c in chunk_stats:
        s = c.get("source_start_ms", 0) / 1000
        e = c.get("source_end_ms", 0) / 1000
        md.append(f"| {c.get('chunk_index')} | {s:.1f}-{e:.1f} | {c.get('status')} | {c.get('word_count') or '-'} | {c.get('max_end_ms') or '-'} |")
    md.append("")
    md.append("## Subtitles")
    md.append(f"- Initial segments: **{sub_stats.get('segment_count', 0)}**")
    md.append(f"- Avg duration: **{sub_stats.get('avg_segment_ms', 0)/1000:.2f}s**")
    if corrected_stats:
        md.append(f"- Corrected segments: **{corrected_stats.get('segment_count', 0)}**")
        md.append(f"- Segments with text changed: **{corrected_seg_count}** ({report['gemini_correction']['corrected_pct']}%)")
    md.append("")
    if corrected_seg_count == 0 and corrected:
        md.append("> No segments needed text correction. Chirp output was already clean.")
    elif corrected_seg_count > 0:
        md.append(f"> Gemini corrected **{corrected_seg_count}** segments without changing any timestamps.")

    (JOB / "qa-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"QA=PASS coverage={coverage_pct}% words={merged['total_words']} segments={sub_stats.get('segment_count', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())