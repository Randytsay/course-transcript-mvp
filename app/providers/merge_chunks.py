"""Create the authoritative Chirp word timeline using midpoint ownership.

Each word is owned by exactly one chunk.  Ownership boundaries are derived
from the actual previous word coverage and the next chunk's start, rather than
from text similarity.  This handles a targeted tail-recovery chunk safely.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
CHUNKS = JOB / "chunks"


def atomic_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def midpoint(word: dict) -> int:
    return (int(word["start_ms"]) + int(word["end_ms"])) // 2


def patch_extends_timeline(merged: list[dict], patch_words: list[dict]) -> bool:
    """Only replace an existing tail range when a patch adds timeline coverage.

    A volume-based QA alert can be caused by music or room noise.  If Chirp
    rechecks that tail but returns no word beyond the already merged timeline,
    replacing the baseline range would lose valid text without repairing the
    gap.  Keep the original words and retain the successful patch as QA
    evidence in that case.
    """
    baseline_end = max((int(word["end_ms"]) for word in merged), default=0)
    patch_end = max((int(word["end_ms"]) for word in patch_words), default=0)
    return patch_end > baseline_end


def repair_chunk_timings(
    manifest: dict, words: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Repair impossible provider timings in the derived merge only.

    Chirp can occasionally return a word whose end is minutes/hours after its
    neighbours, or whose start jumps beyond the chunk window.  Keeping that
    raw word untouched makes the subtitle builder collapse an entire speech
    interval into one giant cue.  Preserve the raw chunk evidence and clamp
    only the derived timing to the next word or the chunk boundary.
    """
    max_word_duration = int(os.environ.get("CHIRP_MAX_WORD_DURATION_MS", "10000"))
    source_start = int(manifest["source_start_ms"])
    source_end = int(manifest["source_end_ms"])
    repaired: list[dict] = []
    repairs: list[dict] = []
    previous_end = source_start
    for offset, original in enumerate(words):
        word = dict(original)
        start = int(word["start_ms"])
        end = int(word["end_ms"])
        original_start, original_end = start, end
        if start > source_end + max_word_duration:
            start = max(source_start, source_end - 40)
        elif start < source_start - max_word_duration:
            start = source_start
        if end <= start or end - start > max_word_duration:
            next_start = None
            if offset + 1 < len(words):
                try:
                    next_start = int(words[offset + 1]["start_ms"])
                except (KeyError, TypeError, ValueError):
                    next_start = None
            if next_start is not None and start < next_start <= source_end + max_word_duration:
                end = next_start
            else:
                end = min(end, start + max_word_duration, source_end)
            if end <= start:
                end = start + 40
        if start < previous_end and original_start >= previous_end:
            start = previous_end
            if end <= start:
                end = start + 40
        word["start_ms"], word["end_ms"] = start, end
        previous_end = max(previous_end, end)
        if (start, end) != (original_start, original_end):
            repairs.append(
                {
                    "chunk_index": manifest["chunk_index"],
                    "word_offset": offset,
                    "word": original.get("word", ""),
                    "original_start_ms": original_start,
                    "original_end_ms": original_end,
                    "repaired_start_ms": start,
                    "repaired_end_ms": end,
                    "reason": "provider_timing_outlier",
                }
            )
        repaired.append(word)
    return repaired, repairs


def load_chunks() -> list[tuple[dict, list[dict]]]:
    result: list[tuple[dict, list[dict]]] = []
    for directory in sorted(CHUNKS.glob("chunk-*")):
        manifest_path, words_path = directory / "manifest.json", directory / "words.json"
        if not manifest_path.exists() or not words_path.exists():
            raise RuntimeError(f"missing manifest or words for {directory.name}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") not in {"SUCCEEDED", "EMPTY_SILENCE"}:
            raise RuntimeError(f"{directory.name} status is {manifest.get('status')}")
        words = json.loads(words_path.read_text(encoding="utf-8")).get("words", [])
        result.append((manifest, words))
    result.sort(key=lambda pair: int(pair[0]["chunk_index"]))
    if not result:
        raise RuntimeError("no completed chunks")
    return result


def main() -> int:
    try:
        all_chunks = load_chunks()
    except RuntimeError as exc:
        print(f"MERGE=FAIL {exc}")
        return 1

    chunks = [pair for pair in all_chunks if pair[0].get("role", "base") == "base"]
    patches = [pair for pair in all_chunks if pair[0].get("role") == "patch"]
    if not chunks:
        print("MERGE=FAIL no base chunks")
        return 1
    valid_words: list[list[dict]] = []
    anomalies: list[dict] = []
    timing_repairs: list[dict] = []
    for manifest, words in chunks:
        clean = []
        for offset, word in enumerate(words):
            start, end = int(word["start_ms"]), int(word["end_ms"])
            if end <= start:
                anomalies.append({"chunk_index": manifest["chunk_index"], "word_offset": offset, "word": word, "reason": "non_positive_duration"})
            else:
                clean.append(word)
        repaired, repairs = repair_chunk_timings(manifest, clean)
        timing_repairs.extend(repairs)
        valid_words.append(repaired)

    # Chunk i owns [lower_boundary, upper_boundary). The boundary is the
    # midpoint of the adjacent source intervals, not a text-similarity result.
    # For 15-minute chunks with a 10-second total overlap this produces
    # 895 / 1785 / 2675 seconds. Empty, verified-silence chunks are valid and
    # therefore cannot be treated as missing transcription evidence.
    boundaries: list[int] = []
    for index in range(len(chunks) - 1):
        previous_manifest = chunks[index][0]
        next_manifest = chunks[index + 1][0]
        previous_end = int(previous_manifest["source_end_ms"])
        next_start = int(next_manifest["source_start_ms"])
        if previous_end < next_start:
            raise RuntimeError(
                f"chunk source intervals have a gap before chunk "
                f"{next_manifest['chunk_index']}"
            )
        boundaries.append((previous_end + next_start) // 2)

    pre_merge = {
        "job": JOB.name,
        "generated_at": datetime.now(UTC).isoformat(),
        "chunks": [
            {"chunk_index": manifest["chunk_index"], "source_start_ms": manifest["source_start_ms"], "source_end_ms": manifest["source_end_ms"], "words": words}
            for (manifest, _), words in zip(chunks, valid_words)
        ],
        "dropped_anomalies": anomalies,
        "timing_repairs": timing_repairs,
    }
    atomic_json(JOB / "pre-merge-words.json", pre_merge)

    merged: list[dict] = []
    decisions: list[dict] = []
    for index, ((manifest, _), words) in enumerate(zip(chunks, valid_words)):
        lower = boundaries[index - 1] if index else None
        upper = boundaries[index] if index < len(boundaries) else None
        kept, rejected = [], 0
        for word in words:
            point = midpoint(word)
            if (lower is not None and point < lower) or (upper is not None and point >= upper):
                rejected += 1
                continue
            kept.append(word)
        merged.extend(kept)
        decisions.append({"chunk_index": manifest["chunk_index"], "lower_midpoint_ms": lower, "upper_midpoint_ms": upper, "input_word_count": len(words), "kept_word_count": len(kept), "rejected_by_ownership": rejected})

    # A targeted patch owns its complete source window. This intentionally
    # replaces any baseline words there, so a known speech gap can be repaired
    # without reprocessing the entire long recording.
    patch_decisions = []
    for manifest, patch_words in sorted(patches, key=lambda pair: int(pair[0]["source_start_ms"])):
        valid_patch = [word for word in patch_words if int(word["end_ms"]) > int(word["start_ms"])]
        start, end = int(manifest["source_start_ms"]), int(manifest["source_end_ms"])
        if not patch_extends_timeline(merged, valid_patch):
            patch_decisions.append({
                "chunk_index": manifest["chunk_index"],
                "source_start_ms": start,
                "source_end_ms": end,
                "baseline_words_replaced": 0,
                "patch_words_inserted": 0,
                "applied": False,
                "reason": "patch_does_not_extend_timeline",
            })
            continue
        before = len(merged)
        merged = [word for word in merged if not (start <= midpoint(word) < end)]
        removed = before - len(merged)
        merged.extend(valid_patch)
        patch_decisions.append({"chunk_index": manifest["chunk_index"], "source_start_ms": start, "source_end_ms": end, "baseline_words_replaced": removed, "patch_words_inserted": len(valid_patch), "applied": True})
    merged.sort(key=lambda word: (int(word["start_ms"]), int(word["end_ms"])))
    if any(int(after["start_ms"]) < int(before["start_ms"]) for before, after in zip(merged, merged[1:])):
        print("MERGE=FAIL non-monotonic merged word starts")
        return 1

    join_qa = []
    for boundary in boundaries:
        join_qa.append({
            "boundary_ms": boundary,
            "window_start_ms": max(0, boundary - 10_000),
            "window_end_ms": boundary + 10_000,
            "words": [word for word in merged if max(0, boundary - 10_000) <= midpoint(word) <= boundary + 10_000],
        })
    atomic_json(JOB / "merge-decisions.json", {"job": JOB.name, "boundaries_ms": boundaries, "decisions": decisions, "patch_decisions": patch_decisions, "dropped_anomalies": anomalies, "timing_repairs": timing_repairs})
    atomic_json(JOB / "join-qa.json", {"job": JOB.name, "joins": join_qa})

    output = {
        "job": JOB.name,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_words": len(merged),
        "total_duration_ms": max((int(word["end_ms"]) for word in merged), default=0),
        "boundaries_ms": boundaries,
        "chunks_merged": [manifest["chunk_index"] for manifest, _ in chunks],
        "dropped_anomaly_count": len(anomalies),
        "timing_repair_count": len(timing_repairs),
        "words": merged,
    }
    atomic_json(JOB / "merged-words.json", output)
    print(f"MERGE=PASS words={len(merged)} joins={len(boundaries)} anomalies_dropped={len(anomalies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
