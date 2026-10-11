#!/usr/bin/env python3
"""Golden24 pilot preflight: read-only audit and fail-closed publish gate.

Never edits Google Drive, candidate transcripts, or immutable backup files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

TIMECODE = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$")
INDEX = re.compile(r"^\d+$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshot(directory: Path) -> dict:
    manifest = directory / "SHA256SUMS.original.txt"
    if not manifest.is_file():
        raise ValueError("Missing immutable snapshot checksum manifest")
    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, separator, filename = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid snapshot checksum line")
        if Path(filename).name != filename or filename in expected:
            raise ValueError("Unsafe or repeated filename in manifest")
        expected[filename] = digest
    required = {
        "20260927.MP3", "20260927.srt", "20260927_逐字稿.txt",
        "20260927_逐字稿.reference-original-70116239.txt",
        "20260927_transcript_report.json",
    }
    if set(expected) != required:
        raise ValueError(f"Backup file set mismatch: missing={sorted(required-set(expected))}")
    mismatched = [name for name, digest in expected.items()
                  if not (directory / name).is_file() or sha256(directory / name) != digest]
    return {"verified": not mismatched, "files": len(expected),
            "mismatched": mismatched, "sha256": expected}


def milliseconds(value: str) -> int:
    match = TIMECODE.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(path: Path) -> tuple[list[dict], list[str]]:
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues, errors = [], []
    previous_end = 0
    for i, block in enumerate(re.split(r"\n\s*\n", content.strip()), 1):
        if not block.strip():
            continue
        lines = block.split("\n")
        if len(lines) < 3 or not INDEX.fullmatch(lines[0].strip()) or " --> " not in lines[1]:
            errors.append(f"Bad cue format at block {i}")
            continue
        number = int(lines[0].strip())
        if number != i:
            errors.append(f"Sequence mismatch at block {i}: {number}")
        try:
            start_s, end_s = lines[1].split(" --> ", 1)
            start, end = milliseconds(start_s.strip()), milliseconds(end_s.strip())
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if end <= start:
            errors.append(f"Nonpositive duration at block {i}")
        if cues and start < previous_end:
            errors.append(f"Overlap at block {i}")
        text = "\n".join(lines[2:]).strip()
        if not text:
            errors.append(f"Blank cue at block {i}")
        cues.append({"id": number, "start_ms": start, "end_ms": end, "text": text,
                     "duration_ms": end - start})
        previous_end = end
    if not cues:
        errors.append("No valid SRT cues")
    return cues, errors


def report_gate(report: dict) -> tuple[list[str], list[str]]:
    """Never trust historical PASS if residual defects are explicitly reported."""
    qa = report.get("qa", {})
    blockers, review = [], []
    if qa.get("status") != "PASS":
        blockers.append("Historical QA is not PASS")
    if qa.get("errors"):
        blockers.append(f"Historical QA errors: {len(qa['errors'])}")
    for key in ("human_missing_count", "source_missing_count"):
        count = int(qa.get(key, -1))
        if count != 0:
            blockers.append(f"{key}={count} (must be resolved with acoustic evidence)")
    boundaries = qa.get("boundary_integrity", {})
    split_count = int(boundaries.get("split_candidate_count", -1))
    if split_count != 0:
        review.append(f"split_candidate_count={split_count}")
    joined = int(boundaries.get("close_unpunctuated_boundary_count", -1))
    if joined:
        review.append(f"close_unpunctuated_boundary_count={joined}")
    if int(qa.get("adjacent_duplicate_count", 0)):
        review.append(f"adjacent_duplicate_count={qa['adjacent_duplicate_count']}")
    if not report.get("timing_truth"):
        blockers.append("No documented timing truth")
    return blockers, review


def audio_duration_ms(path: Path) -> int:
    run = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
                         capture_output=True, text=True, check=True, timeout=30)
    return round(float(run.stdout.strip()) * 1000)


def audit(snapshot_dir: Path) -> dict:
    snap = verify_snapshot(snapshot_dir)
    srt = snapshot_dir / "20260927.srt"
    txt = snapshot_dir / "20260927_逐字稿.txt"
    report = json.loads((snapshot_dir / "20260927_transcript_report.json")
                        .read_text(encoding="utf-8-sig"))
    cues, errors = parse_srt(srt)
    blockers, review = report_gate(report)
    blockers.extend(errors)
    if not snap["verified"]:
        blockers.append("Snapshot SHA-256 verification failed")
    audio_ms = audio_duration_ms(snapshot_dir / "20260927.MP3")
    if cues and cues[-1]["end_ms"] > audio_ms + 1000:
        blockers.append("SRT ends beyond source audio")
    text = txt.read_text(encoding="utf-8-sig")
    if not text.strip():
        blockers.append("TXT is empty")
    gaps = [(a["end_ms"], b["start_ms"]) for a, b in zip(cues, cues[1:])
            if b["start_ms"] - a["end_ms"] > 30000]
    metrics = {
        "cue_count": len(cues),
        "source_audio_ms": audio_ms,
        "subtitle_last_end_ms": cues[-1]["end_ms"] if cues else None,
        "txt_chars": len(text),
        "short_500ms": sum(c["duration_ms"] < 500 for c in cues),
        "long_7000ms": sum(c["duration_ms"] > 7000 for c in cues),
        "long_line_24chars": sum(len(line) > 24 for c in cues for line in c["text"].splitlines()),
        "fast_8cps": sum(c["duration_ms"] > 0 and
                           len(re.sub(r"\s+", "", c["text"])) * 1000 / c["duration_ms"] > 8
                           for c in cues),
        "gaps_gt_30sec": [{"end_ms": a, "next_start_ms": b} for a, b in gaps],
    }
    if metrics["gaps_gt_30sec"]:
        review.append(f"long_subtitle_gaps={len(gaps)} (check voice activity)")
    if metrics["short_500ms"] or metrics["long_7000ms"] or metrics["fast_8cps"]:
        review.append("Display/readability optimization pending")
    # Acoustic confirmation is a separate independent gate. A historic report cannot self-attest.
    blockers.append("Independent audio/Chirp acoustic coverage gate not yet completed")
    blockers.append("Final synchronized SRT/TXT candidate not yet produced and independently verified")
    result = {
        "schema_version": "golden24-preflight-v1",
        "lesson_date": "20260927",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read-only-preflight",
        "source_snapshot": str(snapshot_dir),
        "snapshot_verification": snap,
        "historical_qa_status": report.get("qa", {}).get("status"),
        "metrics": metrics,
        "blockers": sorted(set(blockers)),
        "review_items": sorted(set(review)),
        "status": "REVIEW" if blockers or review else "PASS",
        "publish_allowed": False,  # Only a separate independently evidenced release gate may publish.
        "drive_mutations": 0,
        "next_action": "Independently resolve audio-backed gaps, resegment cues, produce synced candidates, rerun QA",
    }
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    result = audit(args.snapshot.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(args.out)
    print(json.dumps({k: result[k] for k in ("lesson_date", "status",
                                              "publish_allowed", "blockers", "review_items")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
