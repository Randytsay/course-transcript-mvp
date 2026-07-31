"""Submit all Chirp 3 chunks for the job, wait, then merge.

Reads JOB_NAME env var (default voice_11386603-seg1) and computes the chunk
plan from the actual audio duration. Each chunk is 15 minutes long with
10 seconds of total overlap around each merge boundary.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path("/app")
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = ROOT / "data" / "jobs" / JOB_NAME
CHUNKS = JOB / "chunks"

CHUNK_DURATION_S = 900   # 15 minutes per chunk
OVERLAP_S = 10           # total overlap between adjacent chunks
MAX_PARALLEL_CHUNKS = int(os.environ.get("CHIRP_MAX_PARALLEL_CHUNKS", "3"))

KEEP_ENV = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GCS_BUCKET",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "JOB_NAME",
    "LANGUAGE_CODE",
)


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

    Each chunk is at most 15 minutes and overlaps the next chunk by 10 seconds.
    Ownership boundaries are therefore placed at 895, 1785, 2675, ...
    """
    plan: list[tuple[int, float, float]] = []
    index = 0
    start = 0.0
    while start < total_seconds:
        end = min(total_seconds, start + CHUNK_DURATION_S)
        plan.append((index, round(start, 1), round(end, 1)))
        if end >= total_seconds:
            break
        index += 1
        start = end - OVERLAP_S
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


def _source_path() -> Path:
    configured = os.environ.get("SOURCE_MEDIA_PATH")
    if configured:
        path = Path(configured)
        if path.is_file() and path.parent == JOB:
            return path
        raise RuntimeError("SOURCE_MEDIA_PATH must be an existing file inside the job")
    candidates = sorted(JOB.glob("source-original.*"))
    if len(candidates) != 1:
        raise RuntimeError("expected exactly one source-original media file")
    return candidates[0]


def _write_plan(plan: list[tuple[int, float, float]], total_seconds: float) -> None:
    payload = {
        "job": JOB_NAME,
        "duration_seconds": total_seconds,
        "chunk_duration_seconds": CHUNK_DURATION_S,
        "overlap_seconds": OVERLAP_S,
        "chunks": [
            {
                "chunk_index": index,
                "source_start_ms": round(start * 1000),
                "source_end_ms": round(end * 1000),
                "role": "base",
            }
            for index, start, end in plan
        ],
    }
    target = JOB / "chunk-plan.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def process_chunk(index: int, start: float, end: float) -> tuple[int, bool, str]:
    existing = check_chunk(index)
    if existing in {"SUCCEEDED", "EMPTY_SILENCE"}:
        return index, True, f"chunk-{index:03d}: already {existing}"
    chunk_env = {
        "CHUNK_INDEX": str(index),
        "CHUNK_START_SECONDS": str(start),
        "CHUNK_END_SECONDS": str(end),
        "CHUNK_ROLE": "base",
    }
    if existing == "SUBMITTED":
        result = run_subprocess(
            "app.providers.recover_chunk", env_with(chunk_env), timeout=600
        )
    else:
        result = run_subprocess(
            "app.providers.chirp_chunk", env_with(chunk_env), timeout=7200
        )
    message = (result.stdout or "").strip()
    if result.returncode != 0:
        message = f"{message}\n{(result.stderr or '')[:500]}".strip()
    return index, result.returncode == 0, message


def main() -> int:
    print(f"=== Chirp Pipeline: {JOB_NAME} ===")

    # Build a canonical normalized.flac if missing (one-time per job)
    normalized = JOB / "normalized.flac"
    if not normalized.exists():
        try:
            source = _source_path()
        except RuntimeError as exc:
            print(f"PIPELINE=FAIL {exc}")
            return 1
        print("Building normalized.flac (16kHz mono) ...")
        normalize_audio(source, normalized)
    total_seconds = audio_duration_seconds(normalized)
    print(f"Audio duration: {total_seconds:.1f}s ({total_seconds / 60:.2f} min)")

    plan = compute_chunk_plan(total_seconds)
    _write_plan(plan, total_seconds)
    print(f"Chunk plan ({len(plan)} chunks):")
    for index, start, end in plan:
        print(f"  chunk-{index:03d}: {start:.1f}s → {end:.1f}s ({end - start:.0f}s)")

    # Canary: chunk-000 must complete and validate before any parallel requests.
    first_index, first_ok, first_message = process_chunk(*plan[0])
    print(first_message)
    if not first_ok:
        print(f"PIPELINE=FAIL chunk-{first_index:03d} canary failed")
        return 1

    # Remaining chunks may proceed concurrently, bounded by quota-safe config.
    all_succeeded = True
    remaining = plan[1:]
    with ThreadPoolExecutor(max_workers=max(1, MAX_PARALLEL_CHUNKS)) as pool:
        futures = {pool.submit(process_chunk, *item): item[0] for item in remaining}
        for future in as_completed(futures):
            index, succeeded, message = future.result()
            print(message)
            if not succeeded:
                print(f"  chunk-{index:03d}: FAILED")
                all_succeeded = False

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
