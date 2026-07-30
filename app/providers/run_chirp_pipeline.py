"""Submit all Chirp 3 chunks for voice_11386603-seg1, wait, then merge.

Chunk layout (from RUNBOOK.md boundaries 895/1785/2675, with 10s overlap):
  chunk-000:     0s →  905s   (0 to 895+10)
  chunk-001:   885s → 1795s   (895-10 to 1785+10)
  chunk-002:  1775s → 2685s   (1785-10 to 2675+10)
  chunk-003:  2665s → 3349s   (2675-10 to 3349=end of 55:49)

Usage (on VPS):
  export CHUNK_INDEX=0 CHUNK_START_SECONDS=0 CHUNK_END_SECONDS=905
  python -m app.providers.chirp_chunk

Or run all chunks in sequence:
  python -m app.providers.run_chirp_pipeline
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / "voice_11386603-seg1"
CHUNKS = JOB / "chunks"

# (chunk_index, start_seconds, end_seconds)
# overlap=10s on each side of boundaries at 895/1785/2675
CHUNK_PLAN = [
    (0, 0, 905),
    (1, 885, 1795),
    (2, 1775, 2685),
    (3, 2665, 3349),
]

CHECK_INTERVAL_SECONDS = 30


def check_chunk(index: int) -> str | None:
    """Return status if manifest exists, None if not yet."""
    manifest = CHUNKS / f"chunk-{index:03d}" / "manifest.json"
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return data.get("status")


def run_single(index: int, start: float, end: float) -> bool:
    """Run chirp_chunk for one chunk via subprocess."""
    print(f"\n=== Submitting chunk-{index:03d} ({start}s → {end}s) ===")
    result = subprocess.run(
        [
            sys.executable, "-m", "app.providers.chirp_chunk",
        ],
        env={
            "CHUNK_INDEX": str(index),
            "CHUNK_START_SECONDS": str(start),
            "CHUNK_END_SECONDS": str(end),
            **{k: v for k, v in __import__("os").environ.items()
               if k in ("GOOGLE_CLOUD_PROJECT", "GCS_BUCKET",
                        "GOOGLE_APPLICATION_CREDENTIALS")},
        },
        capture_output=True,
        text=True,
        timeout=7200,
    )
    print(result.stdout)
    if result.stderr:
        print(f"STDERR: {result.stderr[:500]}")
    return result.returncode == 0


def recover_chunk(index: int, start: float, end: float) -> bool:
    """Try to recover a chunk from existing GCS results."""
    print(f"\n=== Recovering chunk-{index:03d} from GCS ===")
    result = subprocess.run(
        [
            sys.executable, "-m", "app.providers.recover_chunk",
        ],
        env={
            "CHUNK_INDEX": str(index),
            "CHUNK_START_SECONDS": str(start),
            "CHUNK_END_SECONDS": str(end),
            **{k: v for k, v in __import__("os").environ.items()
               if k in ("GOOGLE_CLOUD_PROJECT", "GCS_BUCKET",
                        "GOOGLE_APPLICATION_CREDENTIALS")},
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    print(result.stdout)
    if result.stderr:
        print(f"STDERR: {result.stderr[:500]}")
    return result.returncode == 0


def main() -> int:
    print("=== Chirp Pipeline: voice_11386603-seg1 ===")
    print(f"Chunk plan: {CHUNK_PLAN}")

    # Phase 1: Submit/process all chunks
    all_succeeded = True
    for index, start, end in CHUNK_PLAN:
        status = check_chunk(index)
        if status == "SUCCEEDED":
            print(f"  chunk-{index:03d}: already SUCCEEDED, skipping")
            continue
        elif status == "SUBMITTED":
            print(f"  chunk-{index:03d}: previously SUBMITTED, attempting recovery...")
            ok = recover_chunk(index, start, end)
        else:
            ok = run_single(index, start, end)

        if not ok:
            print(f"  chunk-{index:03d}: FAILED")
            all_succeeded = False
        else:
            print(f"  chunk-{index:03d}: PASS")

    if not all_succeeded:
        print("\nPIPELINE=FAIL some chunks failed")
        return 1

    # Phase 2: Merge
    print("\n=== Merging chunk results ===")
    result = subprocess.run(
        [sys.executable, "-m", "app.providers.merge_chunks"],
        capture_output=True, text=True, timeout=60,
    )
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
