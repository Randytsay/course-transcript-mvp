"""Lossless Chirp 3 window repair for Golden V2.

The provider's Speech V2 Dynamic Batch response can carry several classes of
defect inside a single window:

* a word whose ``endOffset`` is missing (protobuf zero-value omission);
* a word whose ``startOffset`` is missing;
* a short burst whose offsets were stamped far ahead of its neighbours
  (forward clock spike) while the following words resumed at the earlier
  true position;
* an intra-window backward clock slip where ``endOffset`` jumps backward by
  >10s (e.g. ``763.080s -> 664.600s``) and subsequent words continue from the
  slipped clock until snapping back to the true clock;
* a minor backward jitter (<5s) between adjacent phrases;
* an offset value that overflowed the provider's internal 64-bit counter.

This module repairs timing **without losing any recognized character**, and
records every decision so a reviewer can re-derive the result.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict

_OFFSET_RE = re.compile(r"^(\d+)(?:\.(\d{1,9}))?s$")

#: A single word longer than this in a raw response is a stretched pause/clock
#: artifact (e.g. a filler syllable stretched across a pause).
MAX_WORD_DURATION_MS = 6_000


@dataclass
class RepairNote:
    """One recorded, auditable repair decision."""

    method: str
    reason: str
    orig_index: int
    word: str
    original_start: Any = None
    original_end: Any = None
    start_ms: int = 0
    end_ms: int = 0
    affected: int = 1


@dataclass
class RepairResult:
    """Outcome of repairing one provider window."""

    words: list[dict[str, Any]]
    notes: list[RepairNote] = field(default_factory=list)
    source_characters: int = 0
    repaired_characters: int = 0
    coalesced_pairs: int = 0
    reanchored_runs: int = 0
    overflow_words: int = 0

    @property
    def characters_lost(self) -> int:
        return self.source_characters - self.repaired_characters

    def summary(self) -> dict[str, Any]:
        return {
            "word_count": len(self.words),
            "source_characters": self.source_characters,
            "repaired_characters": self.repaired_characters,
            "characters_lost": self.characters_lost,
            "coalesced_pairs": self.coalesced_pairs,
            "reanchored_runs": self.reanchored_runs,
            "overflow_words": self.overflow_words,
            "repairs": [
                {
                    "method": note.method,
                    "reason": note.reason,
                    "orig_index": note.orig_index,
                    "word": note.word,
                    "original_start": note.original_start,
                    "original_end": note.original_end,
                    "start_ms": note.start_ms,
                    "end_ms": note.end_ms,
                    "affected": note.affected,
                }
                for note in self.notes
            ],
        }


def _offset_ms(value: Any) -> int | None:
    """Parse a Speech ``X[.YYYYY]s`` offset into milliseconds."""
    if not isinstance(value, str):
        return None
    match = _OFFSET_RE.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1)) * 1000 + round(
        int((match.group(2) or "").ljust(9, "0")) / 1_000_000
    )


def _provider_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every provider word entry of a Speech V2 result payload."""
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise V2Conflict("Speech V2 result list missing")
    entries: list[dict[str, Any]] = []
    for result in results:
        alternatives = result.get("alternatives") if isinstance(result, dict) else None
        if not isinstance(alternatives, list):
            raise V2Conflict("Speech recognition alternatives missing")
        if not alternatives:
            continue
        words = alternatives[0].get("words") if isinstance(alternatives[0], dict) else None
        if not isinstance(words, list):
            raise V2Conflict("Speech word timing evidence missing")
        for entry in words:
            if not isinstance(entry, dict):
                raise V2Conflict("Speech word record invalid")
            text = entry.get("word")
            if not isinstance(text, str) or not text:
                raise V2Conflict("Speech word is invalid")
            entries.append(entry)
    if not entries:
        raise V2Conflict("Speech word timing evidence missing")
    return entries


def _coalesce_zero_duration(
    entries: list[dict[str, Any]], window_start_ms: int
) -> tuple[list[dict[str, Any]], list[RepairNote], int]:
    """Join a token that lacks ``endOffset`` with its same-start successor."""
    words: list[dict[str, Any]] = []
    notes: list[RepairNote] = []
    index = 0
    coalesced = 0
    total = len(entries)
    while index < total:
        entry = entries[index]
        text = entry["word"]
        start = _offset_ms(entry.get("startOffset"))
        end = _offset_ms(entry.get("endOffset"))
        if end is None and start is not None:
            merged_text = text
            merged_end = None
            successor = index + 1
            consumed: list[int] = []
            while successor < total:
                candidate = entries[successor]
                candidate_start = _offset_ms(candidate.get("startOffset"))
                if candidate_start != start:
                    break
                merged_text += candidate["word"]
                consumed.append(successor)
                candidate_end = _offset_ms(candidate.get("endOffset"))
                if candidate_end is not None:
                    merged_end = candidate_end
                    break
                successor += 1
            if merged_end is not None:
                notes.append(RepairNote(
                    method="COALESCED_ZERO_DURATION_SEQUENCE",
                    reason="word end offset omitted by provider; joined with "
                           "same-start successor keeping both texts",
                    orig_index=index,
                    word=merged_text,
                    original_start=entry.get("startOffset"),
                    original_end=entry.get("endOffset"),
                    start_ms=window_start_ms + start,
                    end_ms=window_start_ms + merged_end,
                    affected=1 + len(consumed),
                ))
                words.append({
                    "word": merged_text,
                    "start_ms": window_start_ms + start,
                    "end_ms": window_start_ms + merged_end,
                    "_orig_index": index,
                    "_start_offset": entry.get("startOffset"),
                    "_end_offset": entries[successor].get("endOffset"),
                    "_timing_resolution": "COALESCED_ZERO_DURATION_SEQUENCE",
                })
                coalesced += 1
                index = successor + 1
                continue
        words.append({
            "word": text,
            "start_ms": window_start_ms + start if start is not None else None,
            "end_ms": window_start_ms + end if end is not None else None,
            "_orig_index": index,
            "_start_offset": entry.get("startOffset"),
            "_end_offset": entry.get("endOffset"),
        })
        index += 1
    return words, notes, coalesced


def _repair_overflow_and_edges(
    words: list[dict[str, Any]], window_start_ms: int, window_end_ms: int
) -> tuple[list[RepairNote], int]:
    """Repair 64-bit overflow offsets and fill missing start/end offsets."""
    notes: list[RepairNote] = []
    overflow_count = 0
    max_valid_ms = window_end_ms + 15_000
    for pos, word in enumerate(words):
        s = word.get("start_ms")
        e = word.get("end_ms")
        if s is not None and s > max_valid_ms:
            overflow_count += 1
            repaired_s = max(window_start_ms, (e - 80) if (e is not None and e <= max_valid_ms) else window_start_ms)
            notes.append(RepairNote(
                method="OFFSET_OVERFLOW_DETECTED",
                reason="provider startOffset exceeded window ceiling; anchored to endOffset",
                orig_index=word["_orig_index"],
                word=word["word"],
                original_start=word.get("_start_offset"),
                original_end=word.get("_end_offset"),
                start_ms=repaired_s,
                end_ms=e if (e is not None and e <= max_valid_ms) else repaired_s + 80,
            ))
            s = repaired_s
            word["start_ms"] = s
        if e is not None and e > max_valid_ms:
            overflow_count += 1
            repaired_e = min(window_end_ms, (s + 80) if s is not None else window_end_ms)
            notes.append(RepairNote(
                method="OFFSET_OVERFLOW_DETECTED",
                reason="provider endOffset exceeded window ceiling; clamped to window",
                orig_index=word["_orig_index"],
                word=word["word"],
                original_start=word.get("_start_offset"),
                original_end=word.get("_end_offset"),
                start_ms=s or window_start_ms,
                end_ms=repaired_e,
            ))
            e = repaired_e
            word["end_ms"] = e

        if s is None and e is None:
            prev_end = words[pos - 1]["end_ms"] if pos > 0 else window_start_ms
            s = max(window_start_ms, prev_end)
            e = s + 80
            notes.append(RepairNote(
                method="DEFAULT_HEAD_START_FILL",
                reason="word carried neither offset; anchored to previous word end",
                orig_index=word["_orig_index"],
                word=word["word"],
                original_start=word.get("_start_offset"),
                original_end=word.get("_end_offset"),
                start_ms=s,
                end_ms=e,
            ))
        elif s is None:
            assert e is not None
            prev_end = words[pos - 1]["end_ms"] if pos > 0 else window_start_ms
            s = max(window_start_ms, min(prev_end, e - 80) if pos > 0 else e - 80)
            notes.append(RepairNote(
                method="DEFAULT_HEAD_END_FILL",
                reason="word start offset omitted by provider; anchored to its end",
                orig_index=word["_orig_index"],
                word=word["word"],
                original_start=word.get("_start_offset"),
                original_end=word.get("_end_offset"),
                start_ms=s,
                end_ms=e,
            ))
        elif e is None:
            e = s + 80
            notes.append(RepairNote(
                method="DEFAULT_TAIL_END_FILL",
                reason="word end offset unusable; anchored to its start",
                orig_index=word["_orig_index"],
                word=word["word"],
                original_start=word.get("_start_offset"),
                original_end=word.get("_end_offset"),
                start_ms=s,
                end_ms=e,
            ))
        word["start_ms"] = int(s)
        word["end_ms"] = int(e)
    return notes, overflow_count


def _repair_clock_slips_and_bursts(
    words: list[dict[str, Any]], window_start_ms: int, window_end_ms: int
) -> tuple[list[RepairNote], int]:
    """Repair intra-window clock slips, forward bursts, and minor jitters.

    Handles three distinct provider timing anomalies without dropping tokens:
    1. Backward clock slip on a token (``end_ms < start_ms - 10_000``, e.g.
       ``763.080s -> 664.600s`` in 251025 chunk-0009): the token's end is
       repaired to ``start_ms + 80``, and the subsequent slipped run (until the
       clock snaps forward by >50s to resume true time) is linearly mapped into
       ``[slip_onset_end_ms, snap_back_end_ms]``.
    2. Short forward clock spike/burst (a run of <= 25 tokens whose ``start_ms``
       jumps ahead of ``prev_end`` by >30s while the tokens immediately after
       the burst resume near ``prev_end``, e.g. ``raw[997..1003]`` in 251025
       chunk-0008 jumping from ``337.12s`` to ``667.60s`` and resuming at
       ``342.36s``): the short burst is re-anchored into the gap
       ``[prev_end, next_true_start]`` rather than poisoning the high-water
       mark for the rest of the window.
    3. Minor backward jitter (e.g. ``raw[490..494]`` in 251025 chunk-0007
       stepping back 1.16s): re-anchored monotonically from ``prev_end``.
    """
    notes: list[RepairNote] = []
    runs = 0
    total = len(words)
    i = 0
    while i < total:
        w = words[i]
        s, e = w["start_ms"], w["end_ms"]

        # Case 1: Backward clock slip onset (e.g. start=8773080, end=8674600)
        if e < s - 10_000:
            onset_end = s + 80
            w["end_ms"] = onset_end
            notes.append(RepairNote(
                method="CLOCK_SLIP_ONSET_REPAIR",
                reason="word endOffset jumped backward by >10s; repaired onset end",
                orig_index=w["_orig_index"],
                word=w["word"],
                original_start=w.get("_start_offset"),
                original_end=w.get("_end_offset"),
                start_ms=s,
                end_ms=onset_end,
            ))
            # Collect subsequent tokens on the slipped clock until a token snaps
            # back to >= onset_end.
            j = i + 1
            snap_end = None
            while j < total:
                wj = words[j]
                if wj["start_ms"] >= onset_end:
                    snap_end = wj["start_ms"]
                    break
                if wj["end_ms"] >= onset_end and (wj["end_ms"] - wj["start_ms"]) > 50_000:
                    snap_end = wj["end_ms"]
                    j += 1
                    break
                j += 1
            slipped = words[i + 1 : j]
            if slipped:
                target_start = onset_end
                target_end = snap_end if (snap_end is not None and snap_end > target_start) else min(
                    window_end_ms, target_start + max(len(slipped) * 200, 1000)
                )
                raw_s0 = slipped[0]["start_ms"]
                # Last slipped word might be the snap-back word whose end_ms is snap_end
                raw_e_last = (
                    slipped[-1]["start_ms"] + 80
                    if slipped[-1]["end_ms"] - slipped[-1]["start_ms"] > 50_000
                    else slipped[-1]["end_ms"]
                )
                raw_span = max(1, raw_e_last - raw_s0)
                avail_span = max(len(slipped) * 40, target_end - target_start)
                cursor = target_start
                for idx_in_run, sw in enumerate(slipped):
                    rel_s = max(0, sw["start_ms"] - raw_s0)
                    sw_end = (
                        sw["start_ms"] + 80
                        if sw["end_ms"] - sw["start_ms"] > 50_000
                        else sw["end_ms"]
                    )
                    rel_e = max(rel_s + 40, sw_end - raw_s0)
                    mapped_s = max(cursor, target_start + int(rel_s * avail_span / raw_span))
                    mapped_e = max(mapped_s + 40, target_start + int(rel_e * avail_span / raw_span))
                    if idx_in_run == len(slipped) - 1 and snap_end is not None:
                        mapped_e = min(mapped_e, snap_end)
                        mapped_s = min(mapped_s, mapped_e - 40)
                    sw["start_ms"] = mapped_s
                    sw["end_ms"] = mapped_e
                    cursor = mapped_e
                notes.append(RepairNote(
                    method="CLOCK_SLIP_RUN_REANCHOR",
                    reason="re-anchored backward-slipped token run into true time span",
                    orig_index=slipped[0]["_orig_index"],
                    word="".join(x["word"] for x in slipped),
                    original_start=slipped[0].get("_start_offset"),
                    original_end=slipped[-1].get("_end_offset"),
                    start_ms=slipped[0]["start_ms"],
                    end_ms=slipped[-1]["end_ms"],
                    affected=len(slipped),
                ))
                runs += 1
            i = j
            continue

        # Case 2: Forward clock burst (short spike of <= 25 words jumping ahead >30s
        # while subsequent words return near prev_end)
        if i > 0:
            prev_end = words[i - 1]["end_ms"]
            if s - prev_end > 30_000:
                j = i + 1
                while j < min(total, i + 25) and words[j]["start_ms"] >= s:
                    j += 1
                if j < total and prev_end <= words[j]["start_ms"] < s - 15_000:
                    burst = words[i:j]
                    gap_start = prev_end
                    gap_end = words[j]["start_ms"]
                    burst_s0 = burst[0]["start_ms"]
                    burst_e1 = max(burst_s0 + 1, burst[-1]["end_ms"])
                    burst_span = burst_e1 - burst_s0
                    avail = max(len(burst) * 40, gap_end - gap_start)
                    scale = min(1.0, avail / burst_span) if burst_span > 0 else 1.0
                    cursor = gap_start
                    for bw in burst:
                        rel_s = bw["start_ms"] - burst_s0
                        rel_e = max(rel_s + 40, bw["end_ms"] - burst_s0)
                        ns = max(cursor, gap_start + int(rel_s * scale))
                        ne = max(ns + 40, gap_start + int(rel_e * scale))
                        bw["start_ms"] = ns
                        bw["end_ms"] = ne
                        cursor = ne
                    notes.append(RepairNote(
                        method="FORWARD_CLOCK_BURST_REANCHOR",
                        reason="short forward timestamp spike re-anchored into local gap between neighbours",
                        orig_index=burst[0]["_orig_index"],
                        word="".join(x["word"] for x in burst),
                        original_start=burst[0].get("_start_offset"),
                        original_end=burst[-1].get("_end_offset"),
                        start_ms=burst[0]["start_ms"],
                        end_ms=burst[-1]["end_ms"],
                        affected=len(burst),
                    ))
                    runs += 1
                    i = j
                    continue

        # Case 3: Minor backward jitter (word starts before prev_end)
        if i > 0 and w["start_ms"] < words[i - 1]["end_ms"]:
            prev_end = words[i - 1]["end_ms"]
            j = i
            while j < total and words[j]["start_ms"] < prev_end:
                j += 1
            jitter_run = words[i:j]
            next_start = words[j]["start_ms"] if j < total else window_end_ms
            avail = max(len(jitter_run) * 40, next_start - prev_end)
            r_s0 = jitter_run[0]["start_ms"]
            r_e1 = max(r_s0 + 1, jitter_run[-1]["end_ms"])
            r_span = r_e1 - r_s0
            scale = min(1.0, avail / r_span) if r_span > 0 else 1.0
            cursor = prev_end
            for jw in jitter_run:
                dur = max(40, int((jw["end_ms"] - jw["start_ms"]) * scale))
                jw["start_ms"] = cursor
                jw["end_ms"] = cursor + dur
                cursor = jw["end_ms"]
            notes.append(RepairNote(
                method="JITTER_RUN_REANCHOR",
                reason="minor non-monotonic jitter run re-anchored after previous word",
                orig_index=jitter_run[0]["_orig_index"],
                word="".join(x["word"] for x in jitter_run),
                original_start=jitter_run[0].get("_start_offset"),
                original_end=jitter_run[-1].get("_end_offset"),
                start_ms=jitter_run[0]["start_ms"],
                end_ms=jitter_run[-1]["end_ms"],
                affected=len(jitter_run),
            ))
            runs += 1
            i = j
            continue

        if w["end_ms"] <= w["start_ms"]:
            w["end_ms"] = w["start_ms"] + 80
        i += 1

    return notes, runs


def _clamp_stretched_words(
    words: list[dict[str, Any]], window_end_ms: int
) -> list[RepairNote]:
    """Clamp a single word that spans an implausible duration (>6s)."""
    notes: list[RepairNote] = []
    for position, word in enumerate(words):
        duration = word["end_ms"] - word["start_ms"]
        if duration <= MAX_WORD_DURATION_MS:
            continue
        following = words[position + 1] if position + 1 < len(words) else None
        limit = following["start_ms"] if following is not None else window_end_ms
        # A single character filler (e.g. '哎' stretched to 35.04s across a
        # blackboard pause) is clamped to a natural syllable duration (500ms)
        # so the subsequent silent/pause interval is exposed to acoustic QA.
        clamped_end = min(limit, word["start_ms"] + 500)
        notes.append(RepairNote(
            method="STRETCHED_WORD_CLAMP",
            reason="single word spanned >6s; inferred 500ms bound exposes interval for review, not acoustic alignment",
            orig_index=word["_orig_index"],
            word=word["word"],
            original_start=word.get("_start_offset"),
            original_end=word.get("_end_offset"),
            start_ms=word["start_ms"],
            end_ms=clamped_end,
        ))
        word["end_ms"] = max(word["start_ms"] + 80, clamped_end)
    return notes


def _enforce_monotonic(
    words: list[dict[str, Any]], window_start_ms: int, window_end_ms: int
) -> list[RepairNote]:
    """Final guard: strictly monotonic, non-overlapping, within window+250ms."""
    notes: list[RepairNote] = []
    original_bounds = [(word["start_ms"], word["end_ms"]) for word in words]
    max_end = window_end_ms + 250
    previous_end = window_start_ms
    for word in words:
        if word["start_ms"] < previous_end:
            word["start_ms"] = previous_end
        if word["end_ms"] <= word["start_ms"]:
            word["end_ms"] = word["start_ms"] + 40
        if word["end_ms"] > max_end:
            word["end_ms"] = max_end
            if word["start_ms"] >= word["end_ms"]:
                word["start_ms"] = max(window_start_ms, word["end_ms"] - 40)
        previous_end = word["end_ms"]
    # If backward clamping at the tail created any overlap, resolve backwards
    for idx in range(len(words) - 2, -1, -1):
        if words[idx]["end_ms"] > words[idx + 1]["start_ms"]:
            words[idx]["end_ms"] = words[idx + 1]["start_ms"]
            if words[idx]["start_ms"] >= words[idx]["end_ms"]:
                words[idx]["start_ms"] = max(window_start_ms, words[idx]["end_ms"] - 20)
    for position, (word, (start, end)) in enumerate(zip(words, original_bounds)):
        if (word["start_ms"], word["end_ms"]) != (start, end):
            notes.append(RepairNote(
                method="MONOTONIC_BOUNDARY_NORMALIZATION",
                reason="local ordering correction; not acoustically verified",
                orig_index=word.get("_orig_index", position), word=word["word"],
                original_start=start, original_end=end,
                start_ms=word["start_ms"], end_ms=word["end_ms"],
            ))
    return notes


def repair_window(
    raw_bytes: bytes, window: dict[str, Any]
) -> RepairResult:
    """Repair one immutable provider window without losing any text."""
    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, ValueError) as exc:
        raise V2Conflict("provider result JSON invalid") from exc
    if not isinstance(payload, dict):
        raise V2Conflict("provider result JSON invalid")

    window_start_ms = int(window["start_ms"])
    window_end_ms = int(window["end_ms"])
    if window_end_ms <= window_start_ms:
        raise V2Conflict("invalid source window")

    entries = _provider_entries(payload)
    source_characters = sum(len(entry["word"].strip()) for entry in entries)

    words, notes, coalesced = _coalesce_zero_duration(entries, window_start_ms)
    ov_notes, overflow_count = _repair_overflow_and_edges(words, window_start_ms, window_end_ms)
    notes.extend(ov_notes)
    slip_notes, runs = _repair_clock_slips_and_bursts(words, window_start_ms, window_end_ms)
    notes.extend(slip_notes)
    notes.extend(_clamp_stretched_words(words, window_end_ms))
    notes.extend(_enforce_monotonic(words, window_start_ms, window_end_ms))

    repaired_characters = sum(len(word["word"].strip()) for word in words)
    if repaired_characters != source_characters:
        raise V2Conflict(
            f"window repair would lose {source_characters - repaired_characters} "
            "recognized characters; refusing to write a lossy window"
        )
    return RepairResult(
        words=[
            {
                "word": word["word"],
                "start_ms": word["start_ms"],
                "end_ms": word["end_ms"],
            }
            for word in words
        ],
        notes=notes,
        source_characters=source_characters,
        repaired_characters=repaired_characters,
        coalesced_pairs=coalesced,
        reanchored_runs=runs,
        overflow_words=overflow_count,
    )


#: Two windows that overlap on the same audio may report the same word with
#: slightly different offsets. Such a pair is a duplicate, not new content.
DUPLICATE_WORD_TOLERANCE_MS = 400


def merge_base_windows(
    windows: list[dict[str, Any]],
    duration_ms: int,
    *, audit: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge overlapping base windows without dropping any recognized word.

    Adjacent windows overlap on purpose so a provider hiccup at a seam never
    loses audio. A naive "each word belongs to exactly one seam zone" rule
    silently discards every word whose midpoint falls in the overlap band of
    one window while the neighbour does not repeat it. This merge instead
    unions every window's words and removes only true duplicates: the same
    text within :data:`DUPLICATE_WORD_TOLERANCE_MS` of an already-kept word.
    """
    if not windows:
        return []
    ordered = sorted(windows, key=lambda item: item["start_ms"])

    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, item in enumerate(ordered):
        for word in item["words"]:
            candidates.append((word["start_ms"], word["end_ms"], index, word))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    # A word may only be dropped when ANOTHER window recognized the very same
    # text at the very same moment. Two identical words inside one window are
    # two real words (repetition in speech) and must both survive.
    kept: list[tuple[int, int, int, dict[str, Any]]] = []
    # Each retained token may consume at most ONE token from each source
    # window. Otherwise a single neighbour could erase a repeated syllable.
    matched_windows: list[set[int]] = []
    for start, end, index, word in candidates:
        duplicate = False
        for position in range(len(kept) - 1, -1, -1):
            kept_start, _kept_end, kept_index, kept_word = kept[position]
            if start - kept_start > DUPLICATE_WORD_TOLERANCE_MS:
                break
            if (index not in matched_windows[position]
                    and kept_index != index and kept_word["word"] == word["word"]
                    and abs(kept_start - start) <= DUPLICATE_WORD_TOLERANCE_MS):
                matched_windows[position].add(index)
                duplicate = True
                break
        if not duplicate:
            kept.append((start, end, index, word))
            matched_windows.append({index})

    merged: list[dict[str, Any]] = [
        {"word": word["word"], "start_ms": start, "end_ms": end}
        for start, end, _index, word in kept
    ]
    merged.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    clean: list[dict[str, Any]] = []
    previous_end = 0
    for word in merged:
        item = dict(word)
        if item["start_ms"] < previous_end:
            item["start_ms"] = previous_end
        if item["end_ms"] <= item["start_ms"]:
            item["end_ms"] = item["start_ms"] + 40
        if audit is not None and item != word:
            audit.append({
                "method": "MERGE_MONOTONIC_BOUNDARY_NORMALIZATION",
                "word": word["word"],
                "original_start_ms": word["start_ms"],
                "original_end_ms": word["end_ms"],
                "start_ms": item["start_ms"], "end_ms": item["end_ms"],
                "acoustic_timing_verified": False,
            })
        clean.append(item)
        previous_end = item["end_ms"]
    return clean


def build_repair_manifest(
    window: dict[str, Any], result: RepairResult
) -> dict[str, Any]:
    """Build the ``golden-v2-chirp-window-v1`` manifest for a repaired window."""
    manifest = {
        "schema": "golden-v2-chirp-window-v1",
        "job_id": window["job_id"],
        "operation_name": window["operation_name"],
        "request_id": window["request_id"],
        "window_id": window["window_id"],
        "source_sha": window["source_sha"],
        "clip_sha": window["clip_sha"],
        "input_uri": window.get("gcs_uri", window.get("input_uri", "")),
        "output_uri": window["output_uri"],
        "start_ms": int(window["start_ms"]),
        "end_ms": int(window["end_ms"]),
        "raw_sha": window["raw_sha"],
        "words": result.words,
        "actual_cents": int(window["actual_cents"]),
        "acoustic_gate": "NOT_RUN",
    }
    if result.notes:
        manifest["timing_normalizations"] = [
            {
                "method": note.method,
                "reason": note.reason,
                "index": note.orig_index,
                "word": note.word,
                "original_start": note.original_start,
                "original_end": note.original_end,
                "start_ms": note.start_ms,
                "end_ms": note.end_ms,
                "affected": note.affected,
            }
            for note in result.notes
        ]
    return manifest
