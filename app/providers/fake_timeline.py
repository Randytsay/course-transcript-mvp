"""Generate deterministic Chirp-shaped evidence for non-paid integration tests."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ["JOB_NAME"]


def atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    audio = JOB / "normalized.flac"
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration_ms = max(2_000, round(float(result.stdout.strip()) * 1000))
    tokens = list("這是一段不會呼叫付費服務的測試逐字稿。")
    step = max(80, min(300, duration_ms // len(tokens)))
    words = []
    for index, token in enumerate(tokens):
        start = index * step
        end = min(duration_ms, start + max(60, step - 10))
        words.append({"word": token, "start_ms": start, "end_ms": end})
    chunk_dir = JOB / "chunks" / "chunk-000"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "chunk_index": 0,
        "role": "base",
        "status": "SUCCEEDED",
        "source_start_ms": 0,
        "source_end_ms": duration_ms,
        "model": "fake-chirp-3",
        "paid_operation": False,
    }
    atomic(chunk_dir / "manifest.json", manifest)
    atomic(chunk_dir / "chirp-raw.json", {"results": [], "fake": True})
    atomic(chunk_dir / "words.json", {"words": words})
    atomic(
        JOB / "chunk-plan.json",
        {
            "job": JOB.name,
            "duration_seconds": duration_ms / 1000,
            "chunks": [manifest],
        },
    )
    atomic(
        JOB / "pre-merge-words.json",
        {"job": JOB.name, "chunks": [{**manifest, "words": words}]},
    )
    atomic(JOB / "merge-decisions.json", {"boundaries_ms": [], "decisions": []})
    atomic(JOB / "join-qa.json", {"job": JOB.name, "joins": []})
    atomic(
        JOB / "merged-words.json",
        {
            "job": JOB.name,
            "total_words": len(words),
            "total_duration_ms": words[-1]["end_ms"],
            "boundaries_ms": [],
            "chunks_merged": [0],
            "words": words,
        },
    )
    print(f"FAKE_TIMELINE=PASS words={len(words)} paid_operation=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
