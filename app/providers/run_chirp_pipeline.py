"""Submit Chirp 3 chunks, recover private GCS results, then merge.

Dynamic batching supports two durable orchestration modes:

* ``CHIRP_SUBMIT_ONLY=1`` submits every retained chunk operation and exits.
* ``CHIRP_RECOVER_ONCE=1`` performs one non-blocking recovery pass, returning
  exit code 75 while provider results are still pending.

The dynamic pipeline worker uses those modes to release its source lease while
Google schedules work, allowing several source files to be in flight at once.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = DATA_DIR / "jobs" / JOB_NAME
CHUNKS = JOB / "chunks"


def _env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _iso() -> str:
    return datetime.now(UTC).isoformat()


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
DYNAMIC_BATCHING = _env_true("CHIRP_DYNAMIC_BATCHING", default=False)
SUBMIT_ONLY = _env_true("CHIRP_SUBMIT_ONLY", default=False)
RECOVER_ONCE = _env_true("CHIRP_RECOVER_ONCE", default=False)
RECOVERY_POLL_SECONDS = max(
    10,
    int(os.environ.get("CHIRP_RECOVERY_POLL_SECONDS", "120" if DYNAMIC_BATCHING else "20")),
)
RECOVERY_DEADLINE_SECONDS = max(
    3_600,
    int(
        os.environ.get(
            "CHIRP_RECOVERY_DEADLINE_SECONDS",
            "90000" if DYNAMIC_BATCHING else "7200",
        )
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
    "CHIRP_DYNAMIC_BATCHING",
    "CHIRP_SPEECH_MEAN_VOLUME_DB",
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
    """Build standard canary chunks or uniform dynamic-batch chunks."""
    plan: list[tuple[int, float, float]] = []
    first_window = CHUNK_DURATION_S if DYNAMIC_BATCHING else CANARY_DURATION_S
    first_end = min(total_seconds, float(first_window))
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


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_plan(plan: list[tuple[int, float, float]], total_seconds: float) -> None:
    payload = {
        "job": JOB_NAME,
        "duration_seconds": total_seconds,
        "chunk_duration_seconds": CHUNK_DURATION_S,
        "canary_duration_seconds": CANARY_DURATION_S,
        "overlap_seconds": OVERLAP_S,
        "submission_parallelism": MAX_PARALLEL_CHUNKS,
        "recovery_parallelism": MAX_PARALLEL_RECOVERY,
        "processing_strategy": (
            "DYNAMIC_BATCHING" if DYNAMIC_BATCHING else "PROCESSING_STRATEGY_UNSPECIFIED"
        ),
        "dynamic_batching": DYNAMIC_BATCHING,
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
    _atomic_json(JOB / "chunk-plan.json", payload)


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
    if existing in {"SUBMITTED", "RUNNING", "RECOVERING", "CANCEL_REQUESTED"}:
        return index, True, f"chunk-{index:03d}: existing operation retained"
    chunk_env = _chunk_env(index, start, end)
    chunk_env["SUBMIT_ONLY"] = "1"
    result = run_subprocess(
        "app.providers.chirp_chunk",
        env_with(chunk_env),
        timeout=900,
    )
    message = (result.stdout or "").strip()
    if result.returncode != 0:
        message = f"{message}\n{(result.stderr or '')[:500]}".strip()
        return index, False, message
    return index, True, message


def recover_chunk_once(index: int, start: float, end: float) -> tuple[int, str, str]:
    existing = check_chunk(index)
    if existing in {"SUCCEEDED", "EMPTY_SILENCE"}:
        return index, "done", f"chunk-{index:03d}: already {existing}"
    if existing in {None, "FAILED", "CANCELLED"}:
        return index, "failed", f"chunk-{index:03d}: no recoverable retained operation ({existing})"
    chunk_env = _chunk_env(index, start, end)
    chunk_env["ALLOW_PENDING"] = "1"
    result = run_subprocess(
        "app.providers.recover_chunk",
        env_with(chunk_env),
        timeout=900,
    )
    message = (result.stdout or "").strip()
    if result.returncode == 0:
        return index, "done", message
    if result.returncode == 75:
        return index, "pending", message or f"chunk-{index:03d}: pending"
    message = f"{message}\n{(result.stderr or '')[:500]}".strip()
    return index, "failed", message


def _parallel_phase(
    label: str,
    items: list[tuple[int, float, float]],
    function: Callable[[int, float, float], tuple[int, str, str]],
    max_workers: int,
) -> dict[str, int]:
    counts = {"done": 0, "submitted": 0, "pending": 0, "failed": 0}
    if not items:
        return counts
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(function, *item): item[0] for item in items}
        for future in as_completed(futures):
            index, raw_status, message = future.result()
            status = ("submitted" if raw_status else "failed") if isinstance(raw_status, bool) else raw_status
            print(message)
            counts[status] = counts.get(status, 0) + 1
            if status == "failed":
                print(f"  chunk-{index:03d}: {label} FAILED")
    return counts


def _merge() -> int:
    print("\n=== Merging chunk results ===")
    result = run_subprocess("app.providers.merge_chunks", env_with({}), timeout=120)
    print(result.stdout)
    if result.stderr:
        print(f"STDERR: {result.stderr[:500]}")
    if result.returncode != 0:
        print("\nPIPELINE=FAIL merge failed")
        return 1
    _atomic_json(
        JOB / "chirp-complete.json",
        {
            "job": JOB_NAME,
            "completed_at": _iso(),
            "processing_strategy": (
                "DYNAMIC_BATCHING" if DYNAMIC_BATCHING else "PROCESSING_STRATEGY_UNSPECIFIED"
            ),
        },
    )
    print("\nPIPELINE=PASS all chunks merged successfully")
    return 0


def _recover_pass(plan: list[tuple[int, float, float]]) -> int:
    counts = _parallel_phase(
        "recovery",
        plan,
        recover_chunk_once,
        MAX_PARALLEL_RECOVERY,
    )
    if counts["failed"]:
        print("PIPELINE=FAIL some chunk recoveries failed")
        return 1
    if counts["pending"]:
        _atomic_json(
            JOB / "chirp-waiting.json",
            {
                "job": JOB_NAME,
                "checked_at": _iso(),
                "pending_chunks": counts["pending"],
                "completed_chunks": counts["done"],
                "processing_strategy": (
                    "DYNAMIC_BATCHING" if DYNAMIC_BATCHING else "PROCESSING_STRATEGY_UNSPECIFIED"
                ),
            },
        )
        print(
            f"PIPELINE=PENDING completed={counts['done']} pending={counts['pending']}"
        )
        return 75
    (JOB / "chirp-waiting.json").unlink(missing_ok=True)
    return _merge()


def _prepare_plan() -> list[tuple[int, float, float]]:
    normalized = JOB / "normalized.flac"
    if not normalized.exists():
        try:
            source = _source_path()
        except RuntimeError as exc:
            print(f"PIPELINE=FAIL {exc}")
            return []
        print("Building normalized.flac (16kHz mono) ...")
        normalize_audio(source, normalized)
    total_seconds = audio_duration_seconds(normalized)
    print(f"Audio duration: {total_seconds:.1f}s ({total_seconds / 60:.2f} min)")
    plan = compute_chunk_plan(total_seconds)
    _write_plan(plan, total_seconds)
    print(
        f"Chunk plan ({len(plan)} chunks, submit={MAX_PARALLEL_CHUNKS}, "
        f"recover={MAX_PARALLEL_RECOVERY}, dynamic={DYNAMIC_BATCHING}):"
    )
    for index, start, end in plan:
        print(f"  chunk-{index:03d}: {start:.1f}s → {end:.1f}s ({end - start:.0f}s)")
    return plan


def main() -> int:
    print(f"=== Chirp Pipeline: {JOB_NAME} ===")
    plan = _prepare_plan()
    if not plan:
        return 1

    if RECOVER_ONCE:
        return _recover_pass(plan)

    if DYNAMIC_BATCHING:
        submissions = _parallel_phase(
            "submission",
            plan,
            submit_chunk,
            MAX_PARALLEL_CHUNKS,
        )
        if submissions["failed"]:
            print("PIPELINE=FAIL some dynamic chunk submissions failed")
            return 1
        _atomic_json(
            JOB / "chirp-submitted.json",
            {
                "job": JOB_NAME,
                "submitted_at": _iso(),
                "chunk_count": len(plan),
                "processing_strategy": "DYNAMIC_BATCHING",
            },
        )
        if SUBMIT_ONLY:
            print(f"PIPELINE=SUBMITTED chunks={len(plan)} strategy=DYNAMIC_BATCHING")
            return 0
        deadline = time.monotonic() + RECOVERY_DEADLINE_SECONDS
        while True:
            outcome = _recover_pass(plan)
            if outcome != 75:
                return outcome
            if time.monotonic() >= deadline:
                print("PIPELINE=FAIL dynamic batch recovery exceeded deadline")
                return 1
            time.sleep(RECOVERY_POLL_SECONDS)

    # Standard mode retains the short canary gate.
    first_index, first_status, first_message = submit_chunk(*plan[0])
    print(first_message)
    if not first_status:
        print(f"PIPELINE=FAIL chunk-{first_index:03d} canary failed")
        return 1
    deadline = time.monotonic() + RECOVERY_DEADLINE_SECONDS
    while True:
        first_index, first_status, first_message = recover_chunk_once(*plan[0])
        print(first_message)
        if first_status == "done":
            break
        if first_status == "failed" or time.monotonic() >= deadline:
            print(f"PIPELINE=FAIL chunk-{first_index:03d} canary recovery failed")
            return 1
        time.sleep(RECOVERY_POLL_SECONDS)

    remaining = plan[1:]
    submissions = _parallel_phase(
        "submission",
        remaining,
        submit_chunk,
        MAX_PARALLEL_CHUNKS,
    )
    if submissions["failed"]:
        print("\nPIPELINE=FAIL some chunk submissions failed")
        return 1

    while True:
        outcome = _recover_pass(plan)
        if outcome != 75:
            return outcome
        if time.monotonic() >= deadline:
            print("PIPELINE=FAIL chunk recovery exceeded deadline")
            return 1
        time.sleep(RECOVERY_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
