"""Generate invariant correction evidence without any provider request."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.providers.correct_text import atomic_text, timestamp, write_review_terms

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ["JOB_NAME"]


def main() -> int:
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    segments = [
        {
            **segment,
            "corrected_text": segment["raw_text"],
            "text": segment["raw_text"],
            "uncertain_terms": [],
            "corrected": False,
        }
        for segment in raw["segments"]
    ]
    payload = {
        "source": "fake fixed-segment correction",
        "model": "gemini-3.6-flash",
        "fake_provider": True,
        "segment_count": len(segments),
        "corrected_count": 0,
        "total_duration_ms": segments[-1]["end_ms"],
        "segments": segments,
    }
    atomic_text(
        JOB / "subtitles-corrected.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    for suffix, separator in (("srt", ","), ("vtt", ".")):
        header = "WEBVTT\n\n" if suffix == "vtt" else ""
        content = "\n\n".join(
            (
                (f"{index}\n" if suffix == "srt" else "")
                + f"{timestamp(item['start_ms'], separator)} --> "
                + f"{timestamp(item['end_ms'], separator)}\n"
                + item["corrected_text"]
            )
            for index, item in enumerate(segments, 1)
        )
        atomic_text(JOB / f"subtitles-corrected.{suffix}", header + content + "\n")
    atomic_text(
        JOB / "transcript-corrected.txt",
        "\n".join(item["corrected_text"] for item in segments) + "\n",
    )
    atomic_text(
        JOB / "transcript-corrected.md",
        "# 校正逐字稿\n\n"
        + "\n".join(
            f"[{timestamp(item['start_ms'])[:-4]}] {item['corrected_text']}"
            for item in segments
        )
        + "\n",
    )
    glossary = JOB / "glossary"
    glossary.mkdir(parents=True, exist_ok=True)
    atomic_text(
        glossary / "global-terms.json",
        json.dumps(
            {
                "model": "gemini-3.6-flash",
                "fake_provider": True,
                "usage_metadata": {},
                "terms": [],
                "raw_response": "{\"terms\":[]}",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    atomic_text(glossary / "global-terms.csv", "canonical,variants,confidence\n")
    correction = JOB / "correction-v2"
    correction.mkdir(parents=True, exist_ok=True)
    atomic_text(
        correction / "seg-0001.json",
        json.dumps(
            {
                "model": "gemini-3.6-flash",
                "fake_provider": True,
                "raw_response": "{\"segments\":[]}",
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    write_review_terms(segments, [])
    print(f"FAKE_CORRECTION=PASS segments={len(segments)} paid_operation=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
