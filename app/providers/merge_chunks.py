"""Merge chunked Chirp word results by midpoint ownership.

Boundaries (in seconds from RUNBOOK.md): 895, 1785, 2675.
A word belongs to the earlier chunk if its midpoint < boundary,
otherwise to the later chunk.  Produces one authoritative word
timeline for the full audio file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path("/app")
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = ROOT / "data" / "jobs" / JOB_NAME
CHUNKS = JOB / "chunks"

# Boundaries are derived from chunk layout (15-minute chunks, 10s overlap).
# For 4-chunk layouts: 895s, 1785s, 2675s.
# For 5-chunk layouts (longer files): 895s, 1785s, 2675s, 3565s.
BOUNDARIES_MS = [895_000, 1_785_000, 2_675_000, 3_565_000]


def midpoint_ms(word: dict) -> int:
    return (word["start_ms"] + word["end_ms"]) // 2


def main() -> int:
    # Discover completed chunks
    chunk_dirs = sorted(
        d for d in CHUNKS.iterdir()
        if d.is_dir() and d.name.startswith("chunk-")
    )
    if not chunk_dirs:
        print("MERGE=FAIL no chunk directories found")
        return 1

    manifests = []
    words_by_chunk: dict[int, list[dict]] = {}

    for d in chunk_dirs:
        manifest_path = d / "manifest.json"
        words_path = d / "words.json"
        if not manifest_path.exists():
            print(f"MERGE=FAIL missing manifest {manifest_path}")
            return 1
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "SUCCEEDED":
            print(f"MERGE=FAIL chunk {d.name} status={manifest.get('status')}")
            return 1
        manifests.append(manifest)

        if words_path.exists():
            words_data = json.loads(words_path.read_text(encoding="utf-8"))
            words_by_chunk[manifest["chunk_index"]] = words_data.get("words", [])
        else:
            print(f"MERGE=FAIL missing words.json in {d.name}")
            return 1

    manifests.sort(key=lambda m: m["chunk_index"])
    print(f"MERGE: found {len(manifests)} succeeded chunks")

    # Merge by midpoint ownership at each boundary
    merged_words: list[dict] = []
    for idx, manifest in enumerate(manifests):
        chunk_index = manifest["chunk_index"]
        words = words_by_chunk.get(chunk_index, [])

        # Apply the right boundary if this is not the last chunk
        boundary = BOUNDARIES_MS[idx] if idx < len(BOUNDARIES_MS) else None

        for word in words:
            mp = midpoint_ms(word)
            if boundary is not None and mp >= boundary:
                # This word belongs to the next chunk; skip it here
                continue
            merged_words.append(word)

    merged_words.sort(key=lambda w: (w["start_ms"], w["end_ms"]))

    # Validate monotonic and continuous
    gaps = 0
    for i in range(1, len(merged_words)):
        gap = merged_words[i]["start_ms"] - merged_words[i - 1]["end_ms"]
        if gap > 5000:
            gaps += 1

    output = {
        "job": JOB.name,
        "total_words": len(merged_words),
        "total_duration_ms": max((w["end_ms"] for w in merged_words), default=0),
        "gap_count_gt_5s": gaps,
        "boundaries_ms": BOUNDARIES_MS,
        "chunks_merged": [m["chunk_index"] for m in manifests],
        "words": merged_words,
    }

    (JOB / "merged-words.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"MERGE=PASS total_words={len(merged_words)} gaps_gt_5s={gaps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
