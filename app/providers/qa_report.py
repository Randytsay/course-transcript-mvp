"""Strict structural QA. A report is PASS only when publication is safe."""
from __future__ import annotations

import json
import os
import re
import subprocess
from html import escape
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")


def atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def duration_ms() -> int:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(JOB / "normalized.flac")], capture_output=True, text=True, check=True)
    return round(float(result.stdout.strip()) * 1000)


def audible_between(start_ms: int, end_ms: int) -> bool | None:
    if end_ms <= start_ms:
        return False
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            str(JOB / "normalized.flac"),
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
    if not match:
        return None
    return float(match.group(1)) > float(
        os.environ.get("CHIRP_SPEECH_MEAN_VOLUME_DB", "-50")
    )


def tail_patch_verified(end_ms: int, audio_ms: int) -> bool:
    """Return true when a targeted Chirp patch covered the residual tail.

    A volume threshold alone cannot distinguish trailing room noise or a
    recording's decay from speech.  When a successful patch covers the exact
    residual range and has no word after the final subtitle, Chirp has already
    examined that audio.  Keep it as a warning rather than falsely failing QA.
    """
    for manifest_path in (JOB / "chunks").glob("chunk-*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            manifest.get("role") != "patch"
            or manifest.get("status") not in {"SUCCEEDED", "EMPTY_SILENCE"}
            or int(manifest.get("source_start_ms", audio_ms + 1)) > end_ms
            or int(manifest.get("source_end_ms", 0)) < audio_ms
        ):
            continue
        words_path = manifest_path.with_name("words.json")
        try:
            words = json.loads(words_path.read_text(encoding="utf-8")).get("words", [])
        except (OSError, json.JSONDecodeError):
            continue
        if all(int(word.get("end_ms", 0)) <= end_ms for word in words):
            return True
    return False


def segment_quality(segments: list[dict[str, object]]) -> tuple[list[str], dict[str, object]]:
    """Detect provider timing/text collapses before publication.

    A short interjection is valid, but a cue containing hundreds of provider
    words or a multi-second cue containing only one/two words is evidence of a
    malformed Chirp timestamp/merge result.  Keep the offending IDs in the
    report so a targeted chunk retry can be planned without rerunning a file.
    """
    max_words = int(os.environ.get("CHIRP_MAX_SEGMENT_WORDS", "120"))
    low_word_limit = int(os.environ.get("CHIRP_LOW_WORD_COUNT", "2"))
    low_word_duration_ms = int(
        os.environ.get("CHIRP_LOW_WORD_DURATION_MS", "10000")
    )
    errors: list[str] = []
    abnormal: list[dict[str, object]] = []
    for item in segments:
        try:
            word_count = int(item.get("word_count", -1))
            start_ms = int(item.get("start_ms", 0))
            end_ms = int(item.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        duration_ms_value = max(0, end_ms - start_ms)
        reasons: list[str] = []
        if word_count > max_words:
            reasons.append(f"word_count={word_count}>{max_words}")
        if duration_ms_value > low_word_duration_ms and 0 <= word_count <= low_word_limit:
            reasons.append(
                f"duration_ms={duration_ms_value}>{low_word_duration_ms}"
                f" with word_count={word_count}"
            )
        if reasons:
            abnormal.append(
                {
                    "segment_id": item.get("segment_id"),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "word_count": word_count,
                    "duration_ms": duration_ms_value,
                    "reasons": reasons,
                }
            )
    if abnormal:
        preview = ", ".join(
            f"{item['segment_id']}({';'.join(item['reasons'])})"
            for item in abnormal[:10]
        )
        errors.append(f"abnormal subtitle segment quality: {preview}")
    return errors, {
        "thresholds": {
            "max_segment_words": max_words,
            "low_word_count": low_word_limit,
            "low_word_duration_ms": low_word_duration_ms,
        },
        "abnormal_segments": abnormal,
    }


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
    timing_collisions = [
        item["segment_id"]
        for item in segments
        if item.get("timing_collision_merged")
    ]
    if timing_collisions:
        warnings.append(
            "subtitle timing collisions merged into prior cues: "
            f"{len(timing_collisions)}"
        )
    overlaps = [{"before": a["segment_id"], "after": b["segment_id"], "ms": int(a["end_ms"]) - int(b["start_ms"])} for a, b in zip(segments, segments[1:]) if int(b["start_ms"]) < int(a["end_ms"])]
    if overlaps: errors.append(f"subtitle overlaps: {len(overlaps)}")
    long_gaps = [{"before": a["segment_id"], "after": b["segment_id"], "gap_ms": int(b["start_ms"]) - int(a["end_ms"])} for a, b in zip(segments, segments[1:]) if int(b["start_ms"]) - int(a["end_ms"]) > 5000]
    if long_gaps: warnings.append(f"subtitle gaps over 5 seconds: {len(long_gaps)}")
    audio = duration_ms(); end = int(segments[-1]["end_ms"]) if segments else 0; uncovered = max(0, audio - end)
    segment_errors, segment_quality_report = segment_quality(segments)
    errors.extend(segment_errors)
    overrun_limit = int(os.environ.get("CHIRP_MAX_TIMELINE_OVERRUN_MS", "15000"))
    overrun_words = [
        word
        for word in words
        if int(word.get("end_ms", 0)) > audio + overrun_limit
    ]
    if overrun_words:
        errors.append(
            "merged words exceed audio duration: "
            f"{len(overrun_words)} words beyond {overrun_limit}ms tolerance"
        )
    if uncovered > 1000:
        tail_audible = audible_between(end, audio)
        if tail_audible is True and tail_patch_verified(end, audio):
            warnings.append(
                f"audible audio tail verified by targeted Chirp patch: {uncovered}ms"
            )
        elif tail_audible is True:
            errors.append(f"audible audio tail uncovered: {uncovered}ms")
        elif tail_audible is False:
            warnings.append(f"silent audio tail not subtitled: {uncovered}ms")
        else:
            errors.append(f"unable to verify uncovered audio tail: {uncovered}ms")
    correction_invariant = None
    if corrected:
        correction_invariant = len(corrected["segments"]) == len(segments) and all((a["segment_id"], a["start_ms"], a["end_ms"]) == (b["segment_id"], b["start_ms"], b["end_ms"]) for a, b in zip(segments, corrected["segments"]))
        if not correction_invariant: warnings.append("existing correction is stale and requires a new segment-level correction pass")
    report = {"generated_at": datetime.now(UTC).isoformat(), "job": JOB.name, "status": "PASS" if not errors else "FAIL", "errors": errors, "warnings": warnings, "audio": {"duration_ms": audio}, "chirp": {"model": "chirp_3", "word_count": len(words), "timeline_end_ms": merged.get("total_duration_ms"), "dropped_anomaly_count": merged.get("dropped_anomaly_count", 0), "timing_repair_count": merged.get("timing_repair_count", 0), "timeline_overrun_word_count": len(overrun_words)}, "subtitles": {"segment_count": len(segments), "end_ms": end, "uncovered_tail_ms": uncovered, "overlaps": overlaps, "long_gaps": long_gaps, "timing_collision_segments": timing_collisions, "segment_quality": segment_quality_report}, "correction": {"model": "gemini-3.6-flash" if corrected else None, "immutable_structure_preserved": correction_invariant}}
    atomic(JOB / "qa-report.json", report)
    md = [f"# QA Report: {JOB.name}", "", f"Status: **{report['status']}**", "", "## Errors"] + ([f"- {item}" for item in errors] or ["- None"]) + ["", "## Warnings"] + ([f"- {item}" for item in warnings] or ["- None"])
    temporary = JOB / "qa-report.md.tmp"; temporary.write_text("\n".join(md) + "\n", encoding="utf-8"); temporary.replace(JOB / "qa-report.md")
    html = [
        "<!doctype html><html lang=\"zh-Hant\"><meta charset=\"utf-8\">",
        f"<title>QA Report: {escape(JOB.name)}</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;line-height:1.6}code{background:#f3f4f6;padding:2px 5px} .pass{color:#087f5b}.fail{color:#c92a2a}</style>",
        f"<h1>QA Report: {escape(JOB.name)}</h1>",
        f"<p>Status: <strong class=\"{'pass' if report['status'] == 'PASS' else 'fail'}\">{report['status']}</strong></p>",
        "<h2>Errors</h2><ul>",
        *([f"<li>{escape(item)}</li>" for item in errors] or ["<li>None</li>"]),
        "</ul><h2>Warnings</h2><ul>",
        *([f"<li>{escape(item)}</li>" for item in warnings] or ["<li>None</li>"]),
        "</ul></html>",
    ]
    temporary = JOB / "qa_report.html.tmp"
    temporary.write_text("".join(html), encoding="utf-8")
    temporary.replace(JOB / "qa_report.html")
    atomic(JOB / "qa_report.json", report)
    print(f"QA={report['status']} errors={len(errors)} warnings={len(warnings)}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
