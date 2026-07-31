"""Validate all local, publication-ready artifacts without changing them."""
from __future__ import annotations

import csv
import hashlib
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    errors: list[str] = []
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    require_correction = os.environ.get("REQUIRE_CORRECTION", "1") == "1"
    corrected_path = JOB / "subtitles-corrected.json"
    corrected = (
        json.loads(corrected_path.read_text(encoding="utf-8"))
        if corrected_path.exists()
        else {"segments": []}
    )
    raw_segments, corrected_segments = raw.get("segments", []), corrected.get("segments", [])
    if not raw_segments:
        errors.append("raw subtitle segments are empty")
    if require_correction and not corrected_path.exists():
        errors.append("missing corrected subtitle JSON")
    elif corrected_path.exists() and len(raw_segments) != len(corrected_segments):
        errors.append("corrected segment count differs from raw")
    elif corrected_path.exists() and any((a["segment_id"], a["start_ms"], a["end_ms"]) != (b["segment_id"], b["start_ms"], b["end_ms"]) for a, b in zip(raw_segments, corrected_segments)):
        errors.append("corrected segment IDs or timing changed")

    subtitle_files = [
        ("subtitles.srt", SRT_TIMING, True),
        ("subtitles.vtt", VTT_TIMING, False),
    ]
    if corrected_path.exists():
        subtitle_files.extend(
            [
                ("subtitles-corrected.srt", SRT_TIMING, True),
                ("subtitles-corrected.vtt", VTT_TIMING, False),
            ]
        )
    for name, timing, indexed in subtitle_files:
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

    ass_files = ["subtitles.ass"]
    if corrected_path.exists():
        ass_files.append("subtitles-corrected.ass")
    for name in ass_files:
        dialogues = [line for line in (JOB / name).read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue: ")]
        if len(dialogues) != len(raw_segments):
            errors.append(f"{name} dialogue count mismatch")

    with (JOB / "transcript-segments.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(raw_segments):
        errors.append("transcript-segments.csv row count mismatch")
    canonical_exports = [
        "transcript.json",
        "transcript.csv",
        "transcript.docx",
        "transcript.pdf",
        "transcript_raw.txt",
        "transcript_corrected.txt",
        "transcript_timestamped.txt",
        "transcript.srt",
        "transcript.vtt",
        "glossary_candidates.csv",
        "glossary_decisions.yaml",
        "join_qa.json",
        "qa_report.json",
        "qa_report.html",
    ]
    for name in canonical_exports:
        path = JOB / name
        if not path.is_file() or path.stat().st_size <= 0:
            errors.append(f"missing or empty canonical export: {name}")
    manifest_path = JOB / "export-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for artifact in manifest.get("artifacts", []):
            path = JOB / str(artifact.get("name", ""))
            if not path.is_file() or sha256(path) != artifact.get("sha256"):
                errors.append(f"export checksum mismatch: {artifact.get('name')}")
                break
    else:
        errors.append("missing export manifest")

    manifests = sorted((JOB / "chunks").glob("chunk-*/manifest.json"))
    chunk_plan_path = JOB / "chunk-plan.json"
    expected_chunks = (
        len(json.loads(chunk_plan_path.read_text(encoding="utf-8")).get("chunks", []))
        if chunk_plan_path.exists()
        else 1
    )
    manifest_payloads = [
        json.loads(path.read_text(encoding="utf-8")) for path in manifests
    ]
    base_manifests = [
        item for item in manifest_payloads if item.get("role", "base") == "base"
    ]
    if len(base_manifests) != expected_chunks:
        errors.append("missing required chunk manifests")
    for manifest_path, manifest in zip(manifests, manifest_payloads):
        chunk = manifest_path.parent
        if manifest.get("status") not in {"SUCCEEDED", "EMPTY_SILENCE"} or not (chunk / "chirp-raw.json").is_file() or not (chunk / "words.json").is_file():
            errors.append(f"incomplete raw Chirp evidence for {chunk.name}")

    correction_records = list((JOB / "correction-v2").glob("*.json"))
    if require_correction and not correction_records:
        errors.append("missing Gemini correction records")
    for path in correction_records:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("model") != "gemini-3.6-flash" or not isinstance(record.get("raw_response"), str):
            errors.append(f"invalid Gemini evidence: {path.name}")
            break
    if require_correction and not (JOB / "glossary" / "global-terms.json").is_file():
        errors.append("missing global terminology evidence")
    if not (JOB / "join-qa.json").is_file():
        errors.append("missing glossary or join QA")

    print(f"OUTPUT_VALIDATION={'PASS' if not errors else 'FAIL'} errors={len(errors)} segments={len(raw_segments)} correction_records={len(correction_records)}")
    for error in errors:
        print(f"- {error}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
