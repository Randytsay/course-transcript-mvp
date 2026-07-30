"""Align Gemini 3.6 microchunk text to Chirp word timings and emit SRT.

This is deterministic, auditable alignment.  Gemini timestamps are intentionally
not used because Phase 4/5 established that they can exceed the media window.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path("/app")
RESULTS = ROOT / "data" / "results"
GEMINI = RESULTS / "phase6-gemini-3.6-flash-microchunks.json"
CHIRP = RESULTS / "phase3-chirp3-words.json"


def compact(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)


def srt_time(milliseconds: int) -> str:
    milliseconds = max(0, milliseconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def split_cue(text: str, start_ms: int, end_ms: int) -> list[tuple[str, int, int]]:
    text = text.strip()
    if len(text) <= 30:
        return [(text, start_ms, end_ms)]
    pieces = [part.strip() for part in re.split(r"(?<=[，。！？；])", text) if part.strip()]
    if len(pieces) == 1:
        pieces = [text[index:index + 28] for index in range(0, len(text), 28)]
    total = max(1, sum(len(piece) for piece in pieces))
    elapsed, result = 0, []
    for index, piece in enumerate(pieces):
        piece_start = start_ms + round((end_ms - start_ms) * elapsed / total)
        elapsed += len(piece)
        piece_end = end_ms if index == len(pieces) - 1 else start_ms + round((end_ms - start_ms) * elapsed / total)
        result.append((piece, piece_start, max(piece_start + 1, piece_end)))
    return result


def main() -> int:
    gemini = json.loads(GEMINI.read_text(encoding="utf-8"))
    chirp_words = json.loads(CHIRP.read_text(encoding="utf-8"))["words"]
    aligned: list[dict[str, object]] = []
    qa_chunks: list[dict[str, object]] = []

    for chunk_index, chunk in enumerate(gemini["chunks"]):
        source_start = int(chunk["source_start_ms"])
        source_end = int(chunk["source_end_ms"])
        words = [word for word in chirp_words if source_start <= word["start_ms"] < source_end]
        chirp_text = "".join(word["word"] for word in words)
        text_segments = chunk["segments"]
        gemini_text = "".join(segment["text_verbatim"] for segment in text_segments)
        source_matcher = SequenceMatcher(None, compact(gemini_text), compact(chirp_text), autojunk=False)
        matched_chars = sum(block.size for block in source_matcher.get_matching_blocks())
        # Work in compact Gemini character coordinates; map word character
        # coordinates to their deterministic timestamps.
        chirp_positions: list[tuple[int, int, int]] = []
        cursor = 0
        for word in words:
            token = compact(word["word"])
            if token:
                chirp_positions.extend((cursor + offset, int(word["start_ms"]), int(word["end_ms"])) for offset in range(len(token)))
                cursor += len(token)
        matches = [block for block in source_matcher.get_matching_blocks() if block.size]
        gemini_cursor = 0
        last_end = source_start
        for segment in text_segments:
            segment_text = segment["text_verbatim"]
            token = compact(segment_text)
            token_start, token_end = gemini_cursor, gemini_cursor + len(token)
            gemini_cursor = token_end
            mapped = []
            for block in matches:
                overlap_start, overlap_end = max(token_start, block.a), min(token_end, block.a + block.size)
                if overlap_start < overlap_end:
                    mapped.extend(range(block.b + overlap_start - block.a, block.b + overlap_end - block.a))
            valid = [position for position in mapped if position < len(chirp_positions)]
            if valid:
                start_ms = chirp_positions[min(valid)][1]
                end_ms = chirp_positions[max(valid)][2]
                method = "chirp_character_match"
            else:
                # Conservative deterministic fallback inside its known 30-second source window.
                ratio_start = token_start / max(1, len(compact(gemini_text)))
                ratio_end = token_end / max(1, len(compact(gemini_text)))
                start_ms = source_start + round((source_end - source_start) * ratio_start)
                end_ms = source_start + round((source_end - source_start) * ratio_end)
                method = "source_window_fallback"
            start_ms = max(last_end, start_ms)
            end_ms = max(start_ms + 1, end_ms)
            last_end = end_ms
            aligned.append({
                "chunk_index": chunk_index, "start_ms": start_ms, "end_ms": end_ms,
                "speaker": segment["speaker"], "text": segment_text, "timing_method": method,
            })
        qa_chunks.append({
            "chunk_index": chunk_index, "source_start_ms": source_start, "source_end_ms": source_end,
            "gemini_characters": len(compact(gemini_text)), "chirp_characters": len(compact(chirp_text)),
            "matched_characters": matched_chars,
            "match_ratio": round(matched_chars / max(1, len(compact(gemini_text))), 4),
        })

    cues = [cue for segment in aligned for cue in split_cue(str(segment["text"]), int(segment["start_ms"]), int(segment["end_ms"]))]
    srt = "\n\n".join(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}" for index, (text, start, end) in enumerate(cues, 1)) + "\n"
    (RESULTS / "phase7-gemini-3.6-flash-aligned.json").write_text(json.dumps({"model": gemini["model"], "segments": aligned, "qa_chunks": qa_chunks}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "phase7-gemini-3.6-flash-aligned.srt").write_text(srt, encoding="utf-8")
    (RESULTS / "phase7-alignment-qa.json").write_text(json.dumps({"chunks": qa_chunks, "segment_count": len(aligned), "cue_count": len(cues), "fallback_segment_count": sum(s["timing_method"] == "source_window_fallback" for s in aligned)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PHASE7_ALIGN=PASS segments={len(aligned)} cues={len(cues)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
