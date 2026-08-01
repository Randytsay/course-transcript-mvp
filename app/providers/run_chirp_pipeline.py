"""Submit Chirp 3 chunks, recover private GCS results, then merge.

The first chunk is a short canary. Remaining submissions and GCS result
recovery use bounded concurrency. Recovery never resubmits a retained Speech
operation and does not poll the long-running-operation endpoint.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = DATA_DIR / "jobs" / JOB_NAME
CHUNKS = JOB / "chunks"

CHUNK_DURATION_S = 900
CANARY_DURATION_S = max(
    1,
    min(
        CHUNK_DURATION_S,
        int(os.environ.get("CHIRP_CANARY_DURATION_SECONDS", "120")),
    ),
)
OVERLAP_S = 10
MAX_PARALLEL_CHUNKS = max(
    1, int(os.environ.get("CHIRP_MAX_PARALLEL_CHUNKS", "3"))
)
MAX_PARALLEL_RECOVERY = max(
    1,
    min(
        MAX_PARALLEL_CHUNKS,
        int(
            os.environ.get(
                "CHIRP_MAX_PARALLEL_RECOVERY",
                str(MAX_PARALLEL_CHUNKS),
            )
        ),
    ),
)

KEEP_ENV = (
    "COURSE_TRANSCRIPT_DATA_DIR",
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
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return float(result.stdout.strip())


def normalize_audio(source: Path, normalized: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            str(normalized),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def compute_chunk_plan(total_seconds: float) -> list[tuple[int, float, float]]:
    """Build a short first canary, then normal overlapping Chirp chunks."""
    plan: list[tuple[int, float, float]] = []
    first_end = min(total_seconds, float(CANARY_DURATION_S))
    if first_end <= 0:
        return plan
    plan.append((0, 0.0, round(first_end, 1)))
    if first_end >= total_seconds:
        return plan

    index = 1
    start = max(0.0, first_end - OVERLAP_S)
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
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return str(data.get("status") or "") or None


def run_subprocess(
    module: str,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def env_with(chunk_env: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in KEEP_ENV}
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
        "canary_duration_seconds": CANARY_DURATION_S,
        "overlap_seconds": OVERLAP_S,
        "submission_parallelism": MAX_PARALLEL_CHUNKS,
        "recovery_parallelism": MAX_PARALLEL_RECOVERY,
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


def _chunk_env(index: int, start: float, end: float) -> dict[str, str]:
    return {
        "CHUNK_INDEX": str(index),
        "CHUNK_START_SECONDS": str(start),
        "CHUNK_END_SECONDS": str(end),
        "CHUNK_ROLE": "base",
    }


def submit_chunk(index: int, start: float, end: float) -> tuple[int, bool, str]:
    existing = check_chunk(index)
    if existing in {"SUCCEEDED", "EMPTY_SILENCE"}:
        return index, True, f"chunk-{index:03d}: already {existing}"
    if existing in {"SUBMITTED", "RECOVERING", "CANCEL_REQUESTED"}:
        return index, True, f"chunk-{index:03d}: existing operation retained"
    chunk_env = _chunk_env(index, start, end)
    chunk_env["SUBMIT_ONLY"] = "1"
    result = run_subprocess(
        "app.providers.chirp_chunk",
        env_with(chunk_env),
        timeout=600,
    )
    message = (result.stdout or "").strip()
    if result.returncode != 0:
        message = f"{message}\n{(result.stderr or '')[:500]}".strip()
    return index, result.returncode == 0, message


def recover_chunk(index: int, start: float, end: float) -> tuple[int, bool, str]:
    chunk_env = _chunk_env(index, start, end)
    chunk_env["ALLOW_PENDING"] = "1"
    deadline = time.monotonic() + 7200
    while True:
        result = run_subprocess(
            "app.providers.recover_chunk",
            env_with(chunk_env),
            timeout=600,
        )
        message = (result.stdout or "").strip()
        if result.returncode == 0:
            return index, True, message
        if result.returncode == 75 and time.monotonic() < deadline:
            time.sleep(20)
            continue
        if result.returncode != 0:
            message = f"{message}\n{(result.stderr or '')[:500]}".strip()
        return index, False, message


def _parallel_phase(
    label: str,
    items: list[tuple[int, float, float]],
    function: object,
    max_workers: int,
) -> bool:
    if not items:
        return True
    succeeded_all = True
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(function, *item): item[0] for item in items}  # type: ignore[arg-type]
        for future in as_completed(futures):
            index, succeeded, message = future.result()
            print(message)
            if not succeeded:
                print(f"  chunk-{index:03d}: {label} FAILED")
                succeeded_all = False
    return succeeded_all


def main() -> int:
    print(f"=== Chirp Pipeline: {JOB_NAME} ===")
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
    print(
        f"Chunk plan ({len(plan)} chunks, submit={MAX_PARALLEL_CHUNKS}, "
        f"recover={MAX_PARALLEL_RECOVERY}):"
    )
    for index, start, end in plan:
        print(f"  chunk-{index:03d}: {start:.1f}s → {end:.1f}s ({end - start:.0f}s)")

    first_index, first_ok, first_message = submit_chunk(*plan[0])
    print(first_message)
    if not first_ok:
        print(f"PIPELINE=FAIL chunk-{first_index:03d} canary failed")
        return 1
    first_index, first_ok, first_message = recover_chunk(*plan[0])
    print(first_message)
    if not first_ok:
        print(f"PIPELINE=FAIL chunk-{first_index:03d} canary recovery failed")
        return 1

    remaining = plan[1:]
    if not _parallel_phase(
        "submission",
        remaining,
        submit_chunk,
        MAX_PARALLEL_CHUNKS,
    ):
        print("\nPIPELINE=FAIL some chunk submissions failed")
        return 1

    if not _parallel_phase(
        "recovery",
        remaining,
        recover_chunk,
        MAX_PARALLEL_RECOVERY,
    ):
        print("\nPIPELINE=FAIL some chunk recoveries failed")
        return 1

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
