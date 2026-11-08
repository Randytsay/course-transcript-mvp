"""Generate a usable, clearly provisional SRT without treating review flags as a STOP.

Offline, DEV-only, create-new only; no cloud, provider, inference, Ledger,
Production, Drive, IAM or automatic Golden QA. Always preserve every existing
source transcript character. Any retimed spans are labeled PROVISIONAL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from scripts.golden_v2_offline_srt_timeline_qa import (
    Cue, _clock, _visible, merge_short_cues, parse_srt, render_srt,
)

_GAP_MS = 8000
_TEXT_CPS_FOR_RETIME = 18
_REVIEW_MARKER = "【待核對 {index:02d}】此段尚無可靠的語音辨識文字"


def _split_chinese(text: str, *, target: int = 8, maximum: int = 14) -> list[str]:
    parts = []
    while text:
        if len(text) <= maximum:
            parts.append(text)
            break
        low, high = max(5, target - 4), min(maximum, target + 6, len(text) - 1)
        ranked = []
        for at in range(low, high + 1):
            if text[at - 1] in "。！？；":
                bonus = 5
            elif text[at - 1] in "，、,：":
                bonus = 4
            elif text[at - 1] in "」）！?":
                bonus = 3
            elif text[at - 1] == " ":
                bonus = 2
            else:
                bonus = 0
            ranked.append((bonus * 6 - abs(at - target), at))
        at = max(ranked)[1]
        parts.append(text[:at].strip())
        text = text[at:].lstrip()
    return [part for part in parts if part]


def _split_with_times(
    text: str, *, start_ms: int, end_ms: int,
) -> list[dict]:
    if end_ms - start_ms < 500:
        raise ValueError("provisional allocation window too short")
    segments = _split_chinese(text)
    pieces = []
    for j, fragment in enumerate(segments):
        a = start_ms + round((end_ms - start_ms) * j / len(segments))
        b = start_ms + round((end_ms - start_ms) * (j + 1) / len(segments))
        if b <= a:
            raise ValueError("not enough duration for every split")
        pieces.append({"start_ms": a, "end_ms": b, "text": fragment,
                       "provenance": "PROVISIONAL_UNIFORM_TIME_NOT_ACOUSTIC"})
    if _visible("".join(i["text"] for i in pieces)) != _visible(text):
        raise RuntimeError("existing text changed during provisional split")
    return pieces


def create_reviewable_candidate(
    srt_text: str,
    *,
    corrected_txt: str | None = None,
    provisional_retiming: bool = True,
) -> tuple[str, dict]:
    """Return an owner-reviewable candidate and explicit non-Golden warning.

    High-confidence original timestamps remain unchanged, optional high-CPS
    blocks may occupy a bounded portion of the following empty SRT interval.
    The method is NOT forced alignment or proof of spoken words.
    """
    source = parse_srt(srt_text)
    initial_text = _visible("".join(c.text for c in source))
    if corrected_txt is not None and initial_text != _visible(corrected_txt):
        raise ValueError("TXT/SRT source text mismatch")
    merged, merges = merge_short_cues(source)
    # Keep source gaps independently of merge indices (which have changed).
    original_gaps = []
    for before, after in zip(source, source[1:]):
        gap = after.start_ms - before.end_ms
        if gap >= _GAP_MS:
            original_gaps.append({
                "source_cue_before": before.index,
                "source_cue_after": after.index,
                "exact_gap_start_ms": before.end_ms,
                "exact_gap_end_ms": after.start_ms,
                "exact_gap_ms": gap,
            })
    result = []
    approximations = []
    for i, c in enumerate(merged):
        following_start = merged[i + 1].start_ms if i + 1 < len(merged) else c.end_ms
        gap = following_start - c.end_ms
        density = len(_visible(c.text)) * 1000 / (c.end_ms - c.start_ms)
        changed = False
        if provisional_retiming and gap >= _GAP_MS and (
                density > _TEXT_CPS_FOR_RETIME
                or (len(_visible(c.text)) >= 8 and c.end_ms - c.start_ms < 800)
        ):
            count = len(_visible(c.text))
            # Very large collapsed text gets review-friendly provisional times
            # across the whole gap; medium blocks use a 30-s cap. Short text
            # keeps its source timestamps; never invent missing words.
            if count >= 400:
                estimated_end = following_start
            elif count >= 35:
                estimated_end = min(following_start, c.start_ms + 30000)
            elif count >= 8 and c.end_ms - c.start_ms < 800:
                estimated_end = min(following_start, c.start_ms + 3000)
            else:
                estimated_end = c.end_ms
            if estimated_end > c.end_ms:
                try:
                    pieces = _split_with_times(c.text, start_ms=c.start_ms,
                                               end_ms=estimated_end)
                except ValueError:
                    pieces = []
                if pieces:
                    result.extend(pieces)
                    approximations.append({
                        "source_cue_before": next(
                            (original.index for original in source
                             if original.start_ms == c.start_ms),
                            c.index),
                        "start_ms": c.start_ms,
                        "end_ms": estimated_end,
                        "method": "PROVISIONAL_UNIFORM_TIME_NOT_ACOUSTIC",
                        "text_characters": count,
                    })
                    changed = True
        if not changed:
            result.append({
                "start_ms": c.start_ms, "end_ms": c.end_ms,
                "text": c.text, "provenance": "SOURCE_TIMECODE",
            })
        if gap >= _GAP_MS:
            allocated_end = result[-1]["end_ms"]
            if following_start - allocated_end >= 500:
                result.append({
                    "start_ms": allocated_end,
                    "end_ms": following_start,
                    "text": _REVIEW_MARKER.format(index=0),
                    "provenance": "REVIEW_PLACEHOLDER_NOT_SPOKEN",
                })
    # Source cue indices used for placeholders must align with actual gaps.
    # Replace heuristic marker indices with original gap order by start time.
    placeholders = [r for r in result if r["provenance"] ==
                    "REVIEW_PLACEHOLDER_NOT_SPOKEN"]
    if len(placeholders) > len(original_gaps):
        raise RuntimeError("unexpected placeholder count")
    for record in placeholders:
        match = next(
            (j for j, g in enumerate(original_gaps, 1)
             if record["start_ms"] >= g["exact_gap_start_ms"]
             and record["end_ms"] == g["exact_gap_end_ms"]), None)
        if match is None:
            raise RuntimeError("placeholder does not match original gap")
        record["text"] = _REVIEW_MARKER.format(index=match)
    result.sort(key=lambda r: (r["start_ms"], r["end_ms"]))
    for j, c in enumerate(result):
        if c["end_ms"] <= c["start_ms"] or (
            j and c["start_ms"] < result[j - 1]["end_ms"]
        ):
            raise RuntimeError("overlapping or invalid proposed SRT")
    result_text = _visible("".join(
        i["text"] for i in result
        if i["provenance"] != "REVIEW_PLACEHOLDER_NOT_SPOKEN"))
    if result_text != initial_text:
        raise RuntimeError("candidate lost or changed corrected transcript")
    rendered = render_srt([
        Cue(j, c["start_ms"], c["end_ms"], c["text"])
        for j, c in enumerate(result, 1)
    ])
    if len(parse_srt(rendered)) != len(result):
        raise RuntimeError("invalid rendered SRT")
    manifest = {
        "schema": "golden-v2-progressive-reviewable-candidate-v1",
        "delivery_status": "DRAFT_READY_WITH_REVIEW_FLAGS" if original_gaps or approximations
                           else "DRAFT_READY_FOR_OWNER_REVIEW",
        "candidate_output_available": True,
        "human_listening_required_before_draft_output": False,
        "owner_review_flags": original_gaps,
        "provisional_retimed_spans": approximations,
        "short_cue_merge_count": merges,
        "source_cue_count": len(source),
        "candidate_cue_count": len(result),
        "placeholder_cue_count": len(placeholders),
        "source_text_exactly_preserved_except_explicit_markers": True,
        "audio_alignment_verified": False,
        "human_listening_complete": False,
        "golden_qa_pass": False,
        "production_release_authorized": False,
        "drive_publication_authorized": False,
        "warning": ("A reviewable SRT exists even with unresolved source/audio "
                    "alignment. Estimated timings and placeholders are NOT speech evidence."),
    }
    return rendered, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--corrected-txt", type=Path)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--no-provisional-retiming", action="store_true")
    args = parser.parse_args()
    try:
        inputs = [args.srt, args.corrected_txt]
        outputs = [args.candidate_output, args.manifest_output]
        if (len(set(p.resolve() for p in outputs)) != 2
                or any(p.resolve() in {q.resolve() for q in inputs if q}
                       or p.exists() for p in outputs)):
            raise ValueError("refusing overwrite or path collision")
        original = args.srt.read_text(encoding="utf-8-sig")
        txt = (args.corrected_txt.read_text(encoding="utf-8-sig")
               if args.corrected_txt else None)
        rendered, meta = create_reviewable_candidate(
            original, corrected_txt=txt,
            provisional_retiming=not args.no_provisional_retiming,
        )
        # This command never changes its original TXT/SRT/production files.
        args.candidate_output.write_text(rendered, encoding="utf-8")
        args.manifest_output.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({"status":"BLOCKED_INVALID_INPUT",
                          "error_type":type(exc).__name__,
                          "golden_qa_pass":False}))
        return 2
    print(json.dumps({
        "status": meta["delivery_status"],
        "candidate_cue_count": meta["candidate_cue_count"],
        "review_flags": len(meta["owner_review_flags"]),
        "golden_qa_pass": False,
    }))
    # Unlike Golden QA, reviewable-candidate creation succeeds despite flags.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
