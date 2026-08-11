"""Validate raw provider evidence and only the selected user-facing outputs."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from app.jobs.exports import normalize_output_formats

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
SRT_TIMING = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
VTT_TIMING = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")


def blocks(path: Path) -> list[list[str]]:
    return [
        block.splitlines()
        for block in path.read_text(encoding="utf-8").strip().split("\n\n")
        if block.strip()
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _selected() -> list[str]:
    try:
        parsed = json.loads(os.environ.get("OUTPUT_FORMATS_JSON", '["srt","txt"]'))
    except json.JSONDecodeError as exc:
        raise RuntimeError("OUTPUT_FORMATS_JSON is invalid") from exc
    return normalize_output_formats(parsed if isinstance(parsed, list) else None)


def _validate_srt(path: Path, segment_count: int) -> bool:
    units = blocks(path)
    return len(units) == segment_count and all(
        len(unit) >= 3 and unit[0].isdigit() and SRT_TIMING.match(unit[1])
        for unit in units
    )


def _validate_vtt(path: Path, segment_count: int) -> bool:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("WEBVTT\n"):
        return False
    units = blocks(path)
    if units and units[0] == ["WEBVTT"]:
        units = units[1:]
    return len(units) == segment_count and all(
        len(unit) >= 2 and VTT_TIMING.match(unit[0]) for unit in units
    )


def main() -> int:
    errors: list[str] = []
    selected = _selected()
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    require_correction = os.environ.get("REQUIRE_CORRECTION", "1") == "1"
    corrected_path = JOB / "subtitles-corrected.json"
    corrected = (
        json.loads(corrected_path.read_text(encoding="utf-8"))
        if corrected_path.exists()
        else {"segments": []}
    )
    raw_segments = raw.get("segments", [])
    corrected_segments = corrected.get("segments", [])
    if not raw_segments:
        errors.append("raw subtitle segments are empty")
    if require_correction and not corrected_path.exists():
        errors.append("missing corrected subtitle JSON")
    elif corrected_path.exists() and len(raw_segments) != len(corrected_segments):
        errors.append("corrected segment count differs from raw")
    elif corrected_path.exists() and any(
        (a["segment_id"], a["start_ms"], a["end_ms"])
        != (b["segment_id"], b["start_ms"], b["end_ms"])
        for a, b in zip(raw_segments, corrected_segments)
    ):
        errors.append("corrected segment IDs or timing changed")

    cleaned_path = JOB / "subtitles-cleaned.json"
    cleanup_report_path = JOB / "cleanup-review.json"
    if not cleaned_path.exists() or not cleanup_report_path.exists():
        errors.append("missing automatic cleanup evidence")
    else:
        try:
            cleaned = json.loads(cleaned_path.read_text(encoding="utf-8"))
            cleaned_segments = cleaned.get("segments", []) if isinstance(cleaned, dict) else []
            cleanup_report = json.loads(cleanup_report_path.read_text(encoding="utf-8"))
            if len(cleaned_segments) != len(raw_segments):
                errors.append("cleaned segment count differs from raw")
            elif any(
                (a.get("segment_id"), a.get("start_ms"), a.get("end_ms"))
                != (b.get("segment_id"), b.get("start_ms"), b.get("end_ms"))
                for a, b in zip(raw_segments, cleaned_segments)
            ):
                errors.append("cleaned segment IDs or timing changed")
            if any(not str(item.get("cleaned_text", "")).strip() for item in cleaned_segments):
                errors.append("cleaned subtitle contains empty text")
            display_segments = cleaned.get("display_segments", cleaned_segments)
            if not isinstance(display_segments, list) or not display_segments:
                errors.append("display subtitle layer is missing")
            elif any(not str(item.get("cleaned_text", "")).strip() for item in display_segments):
                errors.append("display subtitle contains empty text")
            elif any(
                int(after.get("start_ms", 0)) < int(before.get("end_ms", 0))
                for before, after in zip(display_segments, display_segments[1:])
            ):
                errors.append("display subtitle overlaps")
            if not isinstance(cleanup_report, dict) or not isinstance(cleanup_report.get("review_required", []), list):
                errors.append("cleanup-review.json has invalid review list")
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            errors.append("automatic cleanup evidence is invalid JSON")

    selected_paths = {
        "srt": JOB / "transcript.srt",
        "txt": JOB / "transcript_corrected.txt",
        "csv": JOB / "transcript.csv",
        # ``json`` is the requested raw Chirp sidecar.  It is deliberately
        # separate from the internal ``transcript.json`` evidence so a
        # downstream LLM can consume the immutable provider result.
        "json": JOB / "chirp.json",
        "vtt": JOB / "transcript.vtt",
        "ass": JOB / "subtitles-corrected.ass",
    }
    for output_format in selected:
        path = selected_paths.get(output_format)
        if path is None:
            errors.append(f"unsupported selected output in validator: {output_format}")
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            errors.append(f"missing selected output: {path.name}")
            continue
        if output_format == "srt" and not _validate_srt(path, len(raw_segments)):
            errors.append("transcript.srt cue structure mismatch")
        elif output_format == "vtt" and not _validate_vtt(path, len(raw_segments)):
            errors.append("transcript.vtt cue structure mismatch")
        elif output_format == "csv":
            with path.open(encoding="utf-8", newline="") as stream:
                if len(list(csv.DictReader(stream))) != len(raw_segments):
                    errors.append("transcript.csv row count mismatch")
        elif output_format == "json":
            try:
                sidecar = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append("chirp.json is not valid JSON")
            else:
                sidecar_segments = (
                    sidecar.get("segments", [])
                    if isinstance(sidecar, dict)
                    else []
                )
                if len(sidecar_segments) != len(raw_segments):
                    errors.append("chirp.json segment count mismatch")
        elif output_format == "ass":
            dialogues = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue: ")
            ]
            if len(dialogues) != len(raw_segments):
                errors.append("ASS dialogue count mismatch")

    internal_required = [
        "transcript.json",
        "transcript-segments.csv",
        "glossary_candidates.csv",
        "glossary_decisions.yaml",
        "join_qa.json",
        "subtitles-cleaned.json",
        "cleanup-review.json",
        "qa_report.json",
        "qa_report.html",
    ]
    for name in internal_required:
        path = JOB / name
        if not path.is_file() or path.stat().st_size <= 0:
            errors.append(f"missing or empty internal evidence: {name}")

    manifest_path = JOB / "export-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("selected_output_formats") != selected:
            errors.append("export manifest selected formats mismatch")
        for artifact in manifest.get("artifacts", []):
            path = JOB / str(artifact.get("name", ""))
            if not path.is_file() or sha256(path) != artifact.get("sha256"):
                errors.append(f"export checksum mismatch: {artifact.get('name')}")
                break
    else:
        errors.append("missing export manifest")

    plan_path = JOB / "chunk-plan.json"
    expected_chunks = (
        len(json.loads(plan_path.read_text(encoding="utf-8")).get("chunks", []))
        if plan_path.exists()
        else 0
    )
    manifests = sorted((JOB / "chunks").glob("chunk-*/manifest.json"))
    base_payloads: list[dict[str, Any]] = []
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("role", "base") == "base":
            base_payloads.append(payload)
        chunk = path.parent
        if payload.get("status") not in {"SUCCEEDED", "EMPTY_SILENCE"}:
            errors.append(f"incomplete Chirp status for {chunk.name}")
        if not (chunk / "chirp-raw.json").is_file() or not (chunk / "words.json").is_file():
            errors.append(f"incomplete raw Chirp evidence for {chunk.name}")
    if len(base_payloads) != expected_chunks:
        errors.append("missing required chunk manifests")

    correction_records = list((JOB / "correction-v2").glob("*.json"))
    if require_correction and not correction_records:
        errors.append("missing Gemini correction records")
    for path in correction_records:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("model") != "gemini-3.6-flash" or not isinstance(
            record.get("raw_response"), str
        ):
            errors.append(f"invalid Gemini evidence: {path.name}")
            break
    if require_correction and not (JOB / "glossary" / "global-terms.json").is_file():
        errors.append("missing global terminology evidence")

    print(
        f"OUTPUT_VALIDATION={'PASS' if not errors else 'FAIL'} errors={len(errors)} "
        f"segments={len(raw_segments)} selected={','.join(selected)}"
    )
    for error in errors:
        print(f"- {error}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
