"""Submit all Chirp 3 chunks for the job, wait, then merge.

Reads JOB_NAME env var (default voice_11386603-seg1) and computes the chunk
plan from the actual audio duration. Each chunk is 15 minutes long with
10-second overlap on each side of the merge boundary.

Boundaries (merge at midpoint < b):
  boundary 1 =  895s (between chunk-000 and chunk-001)
  boundary 2 = 1785s (between chunk-001 and chunk-002)
  boundary 3 = 2675s (between chunk-002 and chunk-003)
  boundary 4 = 3565s (between chunk-003 and chunk-004, if audio > 3575s)
  ...

Usage:
  JOB_NAME=voice_11386603-seg2 python -m app.providers.run_chirp_pipeline
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/app")
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = ROOT / "data" / "jobs" / JOB_NAME
CHUNKS = JOB / "chunks"

CHUNK_DURATION_S = 900   # 15 minutes per chunk
OVERLAP_S = 10           # 10 seconds overlap on each side of each boundary
WIDTH_S = CHUNK_DURATION_S + 2 * OVERLAP_S  # 920s per chunk

KEEP_ENV = ("GOOGLE_CLOUD_PROJECT", "GCS_BUCKET", "GOOGLE_APPLICATION_CREDENTIALS", "JOB_NAME")


def audio_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return float(result.stdout.strip())


def normalize_audio(source: Path, normalized: Path) -> None:
    """Create a 16kHz mono flac of the source mp3 for consistent chunking."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(normalized)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def compute_chunk_plan(total_seconds: float) -> list[tuple[int, float, float]]:
    """Compute (index, start_seconds, end_seconds) for each chunk.

    Each chunk spans 15 minutes + 10s overlap on each side of its boundaries.
    Boundaries are placed at 895, 1785, 2675, 3565, ...
    """
    plan: list[tuple[int, float, float]] = []
    index = 0
    cursor = 0.0
    while cursor < total_seconds:
        start = max(0.0, cursor - OVERLAP_S)
        end = min(total_seconds, cursor + CHUNK_DURATION_S + OVERLAP_S)
        plan.append((index, round(start, 1), round(end, 1)))
        index += 1
        cursor += CHUNK_DURATION_S
    return plan


def check_chunk(index: int) -> str | None:
    manifest = CHUNKS / f"chunk-{index:03d}" / "manifest.json"
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return data.get("status")


def run_subprocess(module: str, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def env_with(chunk_env: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV}
    env.update(chunk_env)
    return env


def main() -> int:
    source = JOB / "source.mp3"
    if not source.exists():
        print(f"PIPELINE=FAIL source not found: {source}")
        return 1

    print(f"=== Chirp Pipeline: {JOB_NAME} ===")

    total_seconds = audio_duration_seconds(source)
    print(f"Audio duration: {total_seconds:.1f}s ({total_seconds / 60:.2f} min)")

    # Build a canonical normalized.flac if missing (one-time per job)
    normalized = JOB / "normalized.flac"
    if not normalized.exists():
        print("Building normalized.flac (16kHz mono) ...")
        normalize_audio(source, normalized)

    plan = compute_chunk_plan(total_seconds)
    print(f"Chunk plan ({len(plan)} chunks):")
    for index, start, end in plan:
        print(f"  chunk-{index:03d}: {start:.1f}s → {end:.1f}s ({end - start:.0f}s)")

    # Phase 1: process each chunk
    all_succeeded = True
    for index, start, end in plan:
        existing = check_chunk(index)
        if existing == "SUCCEEDED":
            print(f"  chunk-{index:03d}: already SUCCEEDED, skipping")
            continue
        chunk_env = {
            "CHUNK_INDEX": str(index),
            "CHUNK_START_SECONDS": str(start),
            "CHUNK_END_SECONDS": str(end),
        }
        if existing == "SUBMITTED":
            print(f"  chunk-{index:03d}: previously SUBMITTED, attempting recovery...")
            result = run_subprocess("app.providers.recover_chunk", env_with(chunk_env), timeout=120)
        else:
            print(f"\n=== Submitting chunk-{index:03d} ({start}s → {end}s) ===")
            result = run_subprocess("app.providers.chirp_chunk", env_with(chunk_env), timeout=7200)
        print(result.stdout)
        if result.stderr:
            print(f"STDERR: {result.stderr[:500]}")
        if result.returncode != 0:
            print(f"  chunk-{index:03d}: FAILED")
            all_succeeded = False
        else:
            print(f"  chunk-{index:03d}: PASS")

    if not all_succeeded:
        print("\nPIPELINE=FAIL some chunks failed")
        return 1

    # Phase 2: merge
    print("\n=== Merging chunk results ===")
    result = run_subprocess("app.providers.merge_chunks", env_with({}), timeout=60)
    print(result.stdout)
    if result.stderr:
        print(f"STDERR: {result.stderr[:500]}")
    if result.returncode != 0:
        print("\nPIPELINE=FAIL merge failed")
        return 1

    print("\nPIPELINE=PASS all chunks merged successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())