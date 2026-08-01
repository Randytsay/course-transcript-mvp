"""Repair an audible uncovered audio tail with one private Chirp patch.

The normal long-audio run remains the source of truth.  This module only
submits the residual tail when QA has evidence of speech after the final
merged word.  It reuses a durable patch manifest on retry and never resends a
completed patch operation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.providers.chirp_chunk import has_speech

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
PATCH_INDEX = 999_999
PATCH_OVERLAP_MS = 10_000
MIN_UNCOVERED_MS = 1_000


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def audio_duration_ms() -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(JOB / "normalized.flac"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return round(float(result.stdout.strip()) * 1000)


def tail_window() -> tuple[int, int, int]:
    merged = json.loads((JOB / "merged-words.json").read_text(encoding="utf-8"))
    last_word_end = max(
        (int(word["end_ms"]) for word in merged.get("words", [])), default=0
    )
    audio_end = audio_duration_ms()
    return max(0, last_word_end - PATCH_OVERLAP_MS), audio_end, last_word_end


def _run(module: str, environment: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module],
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def main() -> int:
    start_ms, end_ms, last_word_end = tail_window()
    uncovered_ms = max(0, end_ms - last_word_end)
    report = {
        "job": JOB.name,
        "generated_at": datetime.now(UTC).isoformat(),
        "patch_index": PATCH_INDEX,
        "source_start_ms": start_ms,
        "source_end_ms": end_ms,
        "uncovered_before_ms": uncovered_ms,
        "status": "not_required",
    }
    report_path = JOB / "tail-patch.json"
    if uncovered_ms <= MIN_UNCOVERED_MS:
        atomic_json(report_path, report)
        print("TAIL_PATCH=NOT_REQUIRED")
        return 0

    chunk = JOB / "chunks" / f"chunk-{PATCH_INDEX:03d}"
    audio = chunk / "audio.flac"
    chunk.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            str(JOB / "normalized.flac"),
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-c:a",
            "flac",
            str(audio),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if not has_speech(audio):
        report["status"] = "silent_tail"
        atomic_json(report_path, report)
        audio.unlink(missing_ok=True)
        print("TAIL_PATCH=SILENT")
        return 0

    manifest_path = chunk / "manifest.json"
    prior = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    expected_range = (
        int(prior.get("source_start_ms", -1)) == start_ms
        and int(prior.get("source_end_ms", -1)) == end_ms
        and prior.get("role") == "patch"
    )
    environment = dict(os.environ)
    environment.update(
        {
            "CHUNK_INDEX": str(PATCH_INDEX),
            "CHUNK_START_SECONDS": str(start_ms / 1000),
            "CHUNK_END_SECONDS": str(end_ms / 1000),
            "CHUNK_ROLE": "patch",
        }
    )
    if not expected_range or prior.get("status") not in {"SUCCEEDED", "EMPTY_SILENCE", "SUBMITTED"}:
        environment["SUBMIT_ONLY"] = "1"
        submitted = _run("app.providers.chirp_chunk", environment, timeout=600)
        if submitted.returncode != 0:
            raise RuntimeError((submitted.stdout + "\n" + submitted.stderr).strip())
        report["submission"] = "new"
    elif prior.get("status") in {"SUCCEEDED", "EMPTY_SILENCE"}:
        report["submission"] = "reused_completed"
    else:
        report["submission"] = "reused_submitted"

    environment.pop("SUBMIT_ONLY", None)
    environment["ALLOW_PENDING"] = "1"
    deadline = time.monotonic() + 7_200
    while True:
        recovered = _run("app.providers.recover_chunk", environment, timeout=600)
        if recovered.returncode == 0:
            break
        if recovered.returncode == 75 and time.monotonic() < deadline:
            time.sleep(20)
            continue
        raise RuntimeError((recovered.stdout + "\n" + recovered.stderr).strip())

    merged = _run("app.providers.merge_chunks", dict(os.environ), timeout=600)
    if merged.returncode != 0:
        raise RuntimeError((merged.stdout + "\n" + merged.stderr).strip())
    report["status"] = "patched"
    report["merge"] = merged.stdout.strip()
    atomic_json(report_path, report)
    print(f"TAIL_PATCH=PASS uncovered_before_ms={uncovered_ms}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
