"""Build immutable subtitle segments and text exports from Chirp word timing."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
TARGET_MIN_MS, TARGET_MAX_MS, HARD_GAP_MS = 2_000, 5_000, 1_500
SPLIT_CHARS = set("。！？!?")


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def srt_time(value: int, separator: str = ",") -> str:
    value = max(0, value)
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def segment_words(words: list[dict]) -> list[dict]:
    segments: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        index = len(segments) + 1
        text = "".join(str(word["word"]) for word in current)
        segments.append({"segment_id": f"seg-{index:04d}", "start_ms": int(current[0]["start_ms"]), "end_ms": int(current[-1]["end_ms"]), "raw_text": text, "text": text, "word_count": len(current)})
        current.clear()

    for word in words:
        if not current:
            current.append(word)
            continue
        previous = current[-1]
        gap = int(word["start_ms"]) - int(previous["end_ms"])
        duration = int(word["end_ms"]) - int(current[0]["start_ms"])
        sentence_end = any(char in str(previous["word"]) for char in SPLIT_CHARS)
        if gap >= HARD_GAP_MS or duration >= TARGET_MAX_MS or (duration >= TARGET_MIN_MS and sentence_end) or duration >= TARGET_MIN_MS * 2:
            flush()
        current.append(word)
    flush()
    return segments


def main() -> int:
    merged_path = JOB / "merged-words.json"
    if not merged_path.exists():
        print("BUILD=FAIL merged-words.json missing")
        return 1
    words = json.loads(merged_path.read_text(encoding="utf-8")).get("words", [])
    segments = segment_words(words)
    if not segments or any(segment["end_ms"] <= segment["start_ms"] for segment in segments):
        print("BUILD=FAIL invalid fixed segments")
        return 1
    payload = {"source": "chirp_3_merged", "segment_count": len(segments), "total_duration_ms": segments[-1]["end_ms"], "segments": segments}
    atomic_text(JOB / "subtitles.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    srt = "\n\n".join(f"{index}\n{srt_time(segment['start_ms'])} --> {srt_time(segment['end_ms'])}\n{segment['raw_text']}" for index, segment in enumerate(segments, 1)) + "\n"
    atomic_text(JOB / "subtitles.srt", srt)
    vtt = "WEBVTT\n\n" + "\n\n".join(f"{srt_time(segment['start_ms'], '.')} --> {srt_time(segment['end_ms'], '.')}\n{segment['raw_text']}" for segment in segments) + "\n"
    atomic_text(JOB / "subtitles.vtt", vtt)
    timestamped = "\n".join(f"[{srt_time(segment['start_ms'])[:-4]}] {segment['raw_text']}" for segment in segments) + "\n"
    atomic_text(JOB / "transcript-raw.txt", "\n".join(segment["raw_text"] for segment in segments) + "\n")
    atomic_text(JOB / "transcript-timestamped.txt", timestamped)
    atomic_text(JOB / "transcript-raw.md", "# 原始逐字稿\n\n" + timestamped)
    print(f"BUILD=PASS segments={len(segments)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
