"""Build immutable subtitle segments and text exports from Chirp word timing."""
from __future__ import annotations

import json
import os
from pathlib import Path

import jieba

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
# Chinese ASR may expose one character per timed ``word``.  Subtitle layout
# must therefore work on lexical units, never directly on provider words.
TARGET_MIN_MS, TARGET_MAX_MS, HARD_GAP_MS, MAX_CHARS = 1_200, 6_500, 1_500, 34
PREFERRED_BREAK_CHARS = set("，,、；;：:。！？!?")


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


def lexical_units(words: list[dict]) -> list[dict]:
    """Map Chinese tokenization back to the immutable Chirp word timings."""
    source = "".join(str(word["word"]) for word in words)
    character_to_word: list[int] = []
    for index, word in enumerate(words):
        character_to_word.extend([index] * len(str(word["word"])))
    tokens = jieba.lcut(source, HMM=False)
    if "".join(tokens) != source:
        tokens = list(source)
    units: list[dict] = []
    offset = 0
    for token in tokens:
        if not token:
            continue
        first = character_to_word[offset]
        last = character_to_word[offset + len(token) - 1]
        units.append({
            "text": token,
            "start_ms": int(words[first]["start_ms"]),
            "end_ms": int(words[last]["end_ms"]),
            "word_start": first,
            "word_end": last,
        })
        offset += len(token)
    return units


def segment_words(words: list[dict]) -> list[dict]:
    units = lexical_units(words)
    segments: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        index = len(segments) + 1
        text = "".join(str(unit["text"]) for unit in current)
        start = int(current[0]["start_ms"])
        if segments:
            start = max(start, int(segments[-1]["end_ms"]))
        end = int(current[-1]["end_ms"])
        # Adjacent Chirp words can legitimately share a boundary after chunk
        # merge (for example, an overlap duplicate or two lexical items with
        # identical provider timing).  A new cue would then be clamped to the
        # prior cue end and become zero-length.  Preserve every raw character
        # by attaching that item to the preceding cue instead of emitting an
        # invalid SRT cue or failing the whole job after ASR has succeeded.
        if end <= start:
            if segments:
                previous = segments[-1]
                previous["raw_text"] += text
                previous["text"] += text
                previous["word_count"] += (
                    current[-1]["word_end"] - current[0]["word_start"] + 1
                )
                previous["timing_collision_merged"] = True
                previous.setdefault("timing_collision_word_ranges", []).append(
                    {
                        "word_start": current[0]["word_start"],
                        "word_end": current[-1]["word_end"],
                        "original_start_ms": int(current[0]["start_ms"]),
                        "original_end_ms": end,
                    }
                )
                current.clear()
                return
            # A first provider item with an invalid duration is an upstream
            # anomaly, not a reason to discard the transcript.  Keep it as a
            # minimal valid cue and surface it to QA through its metadata.
            end = start + 1
            timing_collision_merged = True
        else:
            timing_collision_merged = False
        segments.append({
            "segment_id": f"seg-{index:04d}",
            "start_ms": start,
            "end_ms": end,
            "raw_text": text,
            "text": text,
            "word_count": current[-1]["word_end"] - current[0]["word_start"] + 1,
            "timing_collision_merged": timing_collision_merged,
        })
        current.clear()

    for unit in units:
        if not current:
            current.append(unit)
            continue
        previous = current[-1]
        gap = int(unit["start_ms"]) - int(previous["end_ms"])
        prospective_duration = int(unit["end_ms"]) - int(current[0]["start_ms"])
        prospective_chars = sum(len(str(item["text"])) for item in current) + len(str(unit["text"]))
        # jieba can split one provider "word" into several lexical units.  All
        # of those units inherit the same Chirp timing.  Never cut between
        # them: doing so can clamp the next cue's start to the prior cue end
        # and create a zero-duration subtitle segment.
        same_provider_word = unit["word_start"] <= previous["word_end"]
        should_flush = (
            gap >= HARD_GAP_MS
            or (prospective_duration > TARGET_MAX_MS and current)
            or (
                prospective_chars > MAX_CHARS
                and prospective_duration >= TARGET_MIN_MS
            )
        )
        if should_flush and not same_provider_word:
            flush()
        current.append(unit)
        duration = int(current[-1]["end_ms"]) - int(current[0]["start_ms"])
        if duration >= TARGET_MIN_MS and any(char in str(unit["text"]) for char in PREFERRED_BREAK_CHARS):
            flush()
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
