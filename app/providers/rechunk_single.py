"""Re-transcribe a single Chirp chunk and rebuild the merged subtitle pipeline.

This script is intended for targeted quality improvement after an initial
complete job run. It replaces only the words inside the target chunk's source
window, then regenerates the merged timeline, subtitles, Gemini correction,
export artefacts, and QA report so the final outputs stay consistent.

Usage (invoked by the API endpoint, not by the pipeline worker):
    JOB_NAME=<id> CHUNK_INDEX=<n> python -m app.providers.rechunk_single
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "")
CHUNK_INDEX = int(os.environ.get("CHUNK_INDEX", "-1"))


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _atomic_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _run_module(module: str, env: dict[str, str]) -> int:
    result = subprocess.run(
        ["python3", "-m", module],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip()[:1000])
    return result.returncode


def main() -> int:
    if not JOB_NAME:
        print("RECHUNK=FAIL JOB_NAME is not set")
        return 1
    if CHUNK_INDEX < 0:
        print("RECHUNK=FAIL CHUNK_INDEX is not set or invalid")
        return 1

    job_dir = DATA_DIR / "jobs" / JOB_NAME
    chunk_dir = job_dir / "chunks" / f"chunk-{CHUNK_INDEX:03d}"
    plan_path = job_dir / "chunk-plan.json"

    if not job_dir.is_dir():
        print(f"RECHUNK=FAIL job directory not found: {job_dir}")
        return 1

    # --- 1. Load chunk plan to get the time window for this chunk ---
    plan = _load_json(plan_path)
    if not plan or not plan.get("chunks"):
        print("RECHUNK=FAIL chunk-plan.json missing or invalid")
        return 1

    chunk_entry = next(
        (c for c in plan["chunks"] if int(c.get("chunk_index", -1)) == CHUNK_INDEX),
        None,
    )
    if chunk_entry is None:
        print(f"RECHUNK=FAIL chunk-{CHUNK_INDEX:03d} not found in chunk-plan.json")
        return 1

    start_seconds = float(chunk_entry["source_start_ms"]) / 1000.0
    end_seconds = float(chunk_entry["source_end_ms"]) / 1000.0
    dynamic_batching = str(plan.get("processing_strategy", "")) == "DYNAMIC_BATCHING"

    print(f"=== Rechunk Single: job={JOB_NAME} chunk={CHUNK_INDEX:03d} "
          f"window={start_seconds:.1f}s–{end_seconds:.1f}s dynamic={dynamic_batching} ===")

    # --- 2. Wipe the existing chunk manifest so chirp_chunk creates a fresh attempt ---
    manifest_path = chunk_dir / "manifest.json"
    old_manifest = _load_json(manifest_path)
    if old_manifest:
        archive_path = chunk_dir / f"manifest-archived-{_iso().replace(':', '-')}.json"
        _atomic_json(archive_path, old_manifest)
        manifest_path.unlink(missing_ok=True)
        (chunk_dir / "words.json").unlink(missing_ok=True)

    # --- 3. Submit and wait for this single chunk via chirp_chunk_hardened ---
    base_env: dict[str, str] = {
        **os.environ,
        "JOB_NAME": JOB_NAME,
        "CHUNK_INDEX": str(CHUNK_INDEX),
        "CHUNK_START_SECONDS": str(start_seconds),
        "CHUNK_END_SECONDS": str(end_seconds),
        "CHIRP_DYNAMIC_BATCHING": "true" if dynamic_batching else "false",
    }

    print(f"--- Step 1: Re-submitting chunk-{CHUNK_INDEX:03d} to Chirp ---")
    rc = _run_module("app.providers.chirp_chunk_hardened", base_env)
    if rc != 0:
        print(f"RECHUNK=FAIL chirp_chunk_hardened exited {rc}")
        return 1

    # Polling loop using recover_chunk_hardened
    import time
    print(f"--- Polling status for chunk-{CHUNK_INDEX:03d} ---")
    max_attempts = 120  # 10 minutes max
    for attempt in range(max_attempts):
        rc = _run_module("app.providers.recover_chunk_hardened", base_env)
        manifest_after = _load_json(manifest_path)
        status = manifest_after.get("status")
        print(f"Poll {attempt+1}: status={status} exit_code={rc}")
        if status in {"SUCCEEDED", "EMPTY_SILENCE"}:
            break
        if status in {"FAILED", "CANCELLED"}:
            print(f"RECHUNK=FAIL chunk failed with status: {status}")
            return 1
        time.sleep(5)
    else:
        print("RECHUNK=FAIL polling timed out")
        return 1

    word_count = len((_load_json(chunk_dir / "words.json")).get("words", []))
    print(f"chunk-{CHUNK_INDEX:03d}: SUCCEEDED ({word_count} words)")

    # --- 4. Rebuild pipeline: merge → srt → correct → export → qa ---
    pipeline_env = {**base_env}

    print("--- Step 2: Merging all chunks ---")
    rc = _run_module("app.providers.merge_chunks", pipeline_env)
    if rc != 0:
        print(f"RECHUNK=FAIL merge_chunks exited {rc}")
        return 1

    print("--- Step 3: Rebuilding subtitles ---")
    rc = _run_module("app.providers.build_srt", pipeline_env)
    if rc != 0:
        print(f"RECHUNK=FAIL build_srt exited {rc}")
        return 1

    print("--- Step 4: Gemini 3.6 Flash text correction ---")
    rc = _run_module("app.providers.correct_text", pipeline_env)
    if rc != 0:
        print(f"RECHUNK=WARN correct_text exited {rc} (non-fatal)")

    print("--- Step 5: Export formats ---")
    rc = _run_module("app.providers.export_formats", pipeline_env)
    if rc != 0:
        print(f"RECHUNK=WARN export_formats exited {rc} (non-fatal)")

    print("--- Step 6: QA report ---")
    rc = _run_module("app.providers.qa_report", pipeline_env)
    if rc != 0:
        print(f"RECHUNK=WARN qa_report exited {rc} (non-fatal)")

    print(f"RECHUNK=PASS chunk={CHUNK_INDEX:03d} job={JOB_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
