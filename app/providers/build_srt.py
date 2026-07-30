"""Build SRT subtitle file from merged Chirp word timeline.

Strategy: greedily accumulate words into segments, breaking when:
- a hard silence gap (>= 1500ms) is detected between words
- a sentence-ending punctuation is hit
- target segment length is reached

Targets aim for ~2-5s segments with a sane character budget so
Gemini correction has meaningful context per segment.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / "voice_11386603-seg1"

TARGET_MIN_MS = 2_000
TARGET_MAX_MS = 5_000
HARD_GAP_MS = 1_500
SPLIT_CHARS = set("。！？!?")


def srt_time(ms: int) -> str:
    ms = max(0, ms)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def segment_words(words: list[dict]) -> list[dict]:
    segments: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        segments.append({
            "start_ms": current[0]["start_ms"],
            "end_ms": current[-1]["end_ms"],
            "text": "".join(w["word"] for w in current),
            "word_count": len(current),
        })
        current.clear()

    for idx, word in enumerate(words):
        if not current:
            current.append(word)
            continue

        prev = current[-1]
        gap = word["start_ms"] - prev["end_ms"]
        seg_duration = word["end_ms"] - current[0]["start_ms"]
        last_word = prev["word"]
        ends_sentence = any(c in last_word for c in SPLIT_CHARS)

        if gap >= HARD_GAP_MS:
            flush()
        elif seg_duration >= TARGET_MAX_MS:
            flush()
        elif seg_duration >= TARGET_MIN_MS and ends_sentence:
            flush()
        elif seg_duration >= TARGET_MIN_MS * 2:
            flush()

        current.append(word)

    flush()
    return segments


def main() -> int:
    merged_path = JOB / "merged-words.json"
    if not merged_path.exists():
        print("BUILD_SRT=FAIL merged-words.json not found, run merge_chunks first")
        return 1

    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    words = merged["words"]

    segments = segment_words(words)

    # SRT body
    cues = [
        f"{i}\n{srt_time(s['start_ms'])} --> {srt_time(s['end_ms'])}\n{s['text']}"
        for i, s in enumerate(segments, 1)
    ]
    srt_body = "\n\n".join(cues) + "\n"

    out_srt = JOB / "subtitles.srt"
    out_json = JOB / "subtitles.json"
    out_srt.write_text(srt_body, encoding="utf-8")
    out_json.write_text(
        json.dumps(
            {
                "source": "chirp_3_merged",
                "segment_count": len(segments),
                "total_duration_ms": segments[-1]["end_ms"] if segments else 0,
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    avg_dur = (sum(s["end_ms"] - s["start_ms"] for s in segments) / max(1, len(segments))) / 1000
    print(f"BUILD_SRT=PASS segments={len(segments)} avg_seconds={avg_dur:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())