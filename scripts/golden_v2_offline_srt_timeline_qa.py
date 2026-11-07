"""Provider-free, offline Golden V2 SRT timeline audit with conservative merge preview.

NEVER use this as audio alignment or a Golden QA PASS. No network, provider,
ledger, Docker, billing or Drive calls. The optional output is a REVIEW-ONLY
local candidate; it never shifts a timestamp or reconstructs missing speech.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

_CUE_RE = re.compile(
    r"(?P<index>\d+)\n"
    r"(?P<start>\d{2,}:\d{2}:\d{2},\d{3}) --> "
    r"(?P<end>\d{2,}:\d{2}:\d{2},\d{3})\n"
    r"(?P<text>[\s\S]+)\Z"
)
_SHORT_MS = 300
_MAX_MERGE_GAP_MS = 250
_MAX_MERGE_CHARS = 42
_OVERLONG_CHARS = 42
_LARGE_GAP_MS = 8000
_HIGH_CPS = 18.0


@dataclass(frozen=True)
class Cue:
    index: int
    start_ms: int
    end_ms: int
    text: str


def _millis(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    h, m, s, ms = map(int, (hours, minutes, seconds, millis))
    if m >= 60 or s >= 60:
        raise ValueError("invalid SRT clock")
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def _clock(value: int) -> str:
    h, remain = divmod(value, 3_600_000)
    m, remain = divmod(remain, 60_000)
    s, ms = divmod(remain, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _visible(text: str) -> str:
    return re.sub(r"\s+", "", text)


def parse_srt(content: str) -> list[Cue]:
    content = content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        raise ValueError("SRT is empty")
    cues: list[Cue] = []
    for i, block in enumerate(re.split(r"\n[ \t]*\n", content.strip()), 1):
        match = _CUE_RE.fullmatch(block)
        if match is None:
            raise ValueError(f"invalid SRT cue at block {i}")
        index = int(match["index"])
        if index != i:
            raise ValueError(f"nonsequential SRT cue index at block {i}")
        start, end = _millis(match["start"]), _millis(match["end"])
        if end <= start:
            raise ValueError(f"nonpositive cue duration at block {i}")
        if cues and start < cues[-1].end_ms:
            raise ValueError(f"overlapping or out-of-order cue at block {i}")
        cues.append(Cue(index, start, end, match["text"]))
    return cues


def render_srt(cues: list[Cue]) -> str:
    return "\n\n".join(
        f"{i}\n{_clock(c.start_ms)} --> {_clock(c.end_ms)}\n{c.text}"
        for i, c in enumerate(cues, 1)
    ) + "\n"


def summarize(cues: list[Cue], *, reference: str | None = None) -> dict:
    short = []
    fast = []
    overlong = []
    gaps = []
    for i, c in enumerate(cues):
        length = len(_visible(c.text))
        duration_ms = c.end_ms - c.start_ms
        if duration_ms < _SHORT_MS:
            short.append(c.index)
        if length > _OVERLONG_CHARS:
            overlong.append({
                "cue": c.index, "characters": length,
                "duration_ms": duration_ms, "start": _clock(c.start_ms),
                "end": _clock(c.end_ms),
            })
        cps = length * 1000 / duration_ms
        if cps > _HIGH_CPS:
            fast.append({
                "cue": c.index, "characters_per_second": round(cps, 2),
                "duration_ms": duration_ms,
            })
        if i > 0:
            gap_ms = c.start_ms - cues[i - 1].end_ms
            if gap_ms >= _LARGE_GAP_MS:
                gaps.append({
                    "after_cue": cues[i - 1].index, "before_cue": c.index,
                    "duration_ms": gap_ms,
                    "gap_start": _clock(cues[i - 1].end_ms),
                    "gap_end": _clock(c.start_ms),
                    "requires_audio_inspection": True,
                })
    result = {
        "schema": "golden-v2-srt-offline-timeline-qa-v1",
        "cue_count": len(cues),
        "first_cue_start": _clock(cues[0].start_ms),
        "last_cue_end": _clock(cues[-1].end_ms),
        "short_cue_count": len(short),
        "short_cue_indices_sample": short[:30],
        "high_cps_count": len(fast),
        "high_cps_worst": sorted(fast, key=lambda x: -x["characters_per_second"])[:20],
        "overlong_text_cues": overlong,
        "large_gaps": gaps,
        "reference_text_exact_ignoring_whitespace": (
            _visible("".join(c.text for c in cues)) == _visible(reference)
            if reference is not None else None
        ),
        "audio_aligned": False,
        "human_listening_verified": False,
        "golden_qa_pass": False,
        "publication_authorized": False,
        "status": "NEEDS_AUDIO_REVIEW",
    }
    return result


def build_review_interval_manifest(
    cues: list[Cue],
    *,
    provider_evidence: dict | None = None,
    expected_source_sha: str | None = None,
    audio_duration_ms: int | None = None,
    pad_ms: int = 3000,
) -> dict:
    """Describe exact SRT holes separately from *suggested* padded listening.

    Provider evidence, when used, must represent metadata-only word timebounds
    from the exact normalized audio SHA. Nothing here infers audible speech,
    invents word times, repairs cues or certifies a Golden delivery.
    """
    if not cues:
        raise ValueError("no cues to review")
    if pad_ms < 0 or pad_ms > 30000:
        raise ValueError("review padding must be 0..30000 ms")
    if audio_duration_ms is not None and (
        type(audio_duration_ms) is not int or audio_duration_ms < cues[-1].end_ms
    ):
        raise ValueError("verified audio duration ends before SRT")
    provider_words: list[tuple[int, int]] = []
    inventory_complete = False
    if provider_evidence is not None:
        if not isinstance(provider_evidence, dict) or provider_evidence.get("schema") != (
            "golden-v2-source-bound-word-timing-evidence-v1"
        ):
            raise ValueError("unrecognized provider evidence schema")
        source_sha = provider_evidence.get("source_sha256")
        if (
            not isinstance(source_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha)
            or not isinstance(expected_source_sha, str)
            or source_sha != expected_source_sha
        ):
            raise ValueError("provider source SHA is missing or mismatched")
        inventory_complete = provider_evidence.get("complete_source_word_inventory")
        if type(inventory_complete) is not bool:
            raise ValueError("word inventory completeness must be an explicit boolean")
        words = provider_evidence.get("words")
        if not isinstance(words, list) or len(words) > 1000000:
            raise ValueError("word timing evidence must be a bounded list")
        for word in words:
            if not isinstance(word, dict):
                raise ValueError("word evidence must contain numeric time bounds")
            start, end = word.get("start_ms"), word.get("end_ms")
            if type(start) is not int or type(end) is not int or not 0 <= start < end:
                raise ValueError("invalid word time bounds")
            if audio_duration_ms is not None and end > audio_duration_ms:
                raise ValueError("word evidence extends beyond verified audio")
            provider_words.append((start, end))

    intervals = []
    for before, after in zip(cues, cues[1:]):
        gap_ms = after.start_ms - before.end_ms
        if gap_ms < _LARGE_GAP_MS:
            continue
        overlap_count = sum(
            start < after.start_ms and end > before.end_ms
            for start, end in provider_words
        )
        if provider_evidence is None:
            evidence_state = "PROVIDER_WORD_EVIDENCE_NOT_SUPPLIED"
        elif overlap_count:
            evidence_state = "PROVIDER_ANCHOR_WITHIN_SRT_GAP"
        elif inventory_complete:
            evidence_state = "EVIDENCE_MISSING_NO_PROVIDER_WORD_ANCHOR"
        else:
            evidence_state = "UNKNOWN_PARTIAL_PROVIDER_INVENTORY"
        padded_start = max(0, before.end_ms - pad_ms)
        padded_end = after.start_ms + pad_ms
        if audio_duration_ms is not None:
            padded_end = min(audio_duration_ms, padded_end)
        intervals.append({
            "cue_before": before.index,
            "cue_after": after.index,
            "srt_gap_start": _clock(before.end_ms),
            "srt_gap_end": _clock(after.start_ms),
            "srt_gap_ms": gap_ms,
            "review_start": _clock(padded_start),
            "review_end": _clock(padded_end),
            "review_padding_ms_each_side": pad_ms,
            "provider_word_anchor_count": overlap_count if provider_evidence is not None else None,
            "provider_evidence_state": evidence_state,
            "requires_audio_listening": True,
            "text_alignment_repaired": False,
        })
    return {
        "schema": "golden-v2-srt-audio-review-intervals-v1",
        "source_sha_verified_against_evidence": provider_evidence is not None,
        "provider_inventory_complete": inventory_complete if provider_evidence is not None else None,
        "audio_duration_ms": audio_duration_ms,
        "intervals": intervals,
        "interval_count": len(intervals),
        "total_srt_gap_ms": sum(i["srt_gap_ms"] for i in intervals),
        "interpolation_performed": False,
        "human_listening_verified": False,
        "golden_qa_pass": False,
        "publication_authorized": False,
        "status": "EVIDENCE_MISSING_OR_UNREVIEWED",
    }


def merge_short_cues(cues: list[Cue]) -> tuple[list[Cue], int]:
    """Merge only nearly adjoining cues; never interpolate/move a timecode."""
    updated = list(cues)
    merges = 0
    while True:
        changed = False
        for i, c in enumerate(updated):
            if c.end_ms - c.start_ms >= _SHORT_MS:
                continue
            for neighbor in (i + 1, i - 1):
                if neighbor < 0 or neighbor >= len(updated):
                    continue
                a, b = (i, neighbor) if i < neighbor else (neighbor, i)
                left, right = updated[a], updated[b]
                gap = right.start_ms - left.end_ms
                if not 0 <= gap <= _MAX_MERGE_GAP_MS:
                    continue
                if len(_visible(left.text + right.text)) > _MAX_MERGE_CHARS:
                    continue
                combined = Cue(left.index, left.start_ms, right.end_ms,
                               left.text + right.text)
                updated[a:b + 1] = [combined]
                merges += 1
                changed = True
                break
            if changed:
                break
        if not changed:
            break
    # Preserve exactly the original visible characters and temporal ordering.
    if _visible("".join(c.text for c in updated)) != _visible("".join(c.text for c in cues)):
        raise RuntimeError("merge altered transcript text")
    return [Cue(i, c.start_ms, c.end_ms, c.text) for i, c in enumerate(updated, 1)], merges


def inspect(
    srt_text: str, *, reference: str | None = None,
    draft_merge_short: bool = False,
) -> tuple[dict, str | None]:
    cues = parse_srt(srt_text)
    report = summarize(cues, reference=reference)
    draft = None
    if draft_merge_short:
        merged, count = merge_short_cues(cues)
        draft = render_srt(merged)
        report["safe_adjacent_merges"] = count
        report["candidate"] = summarize(merged, reference=reference)
        report["candidate"]["status"] = "NEEDS_AUDIO_REVIEW"
        report["candidate"]["timestamp_interpolation"] = False
        report["candidate"]["nonfinal_preview_only"] = True
    return report, draft


def _write_exclusive(path: Path, content: str, *, input_file: Path) -> None:
    if path.resolve() == input_file.resolve():
        raise ValueError("refusing to overwrite source SRT")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--reference-txt", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--draft-output", type=Path)
    parser.add_argument("--review-intervals-output", type=Path)
    parser.add_argument("--provider-words-json", type=Path,
                        help="optional private metadata-only word bounds (no transcript/audio)")
    parser.add_argument("--expected-source-sha",
                        help="required when provider-words-json is used")
    parser.add_argument("--audio-duration-ms", type=int,
                        help="verified duration from same normalized audio source")
    args = parser.parse_args()
    try:
        outputs = [p for p in (args.report_output, args.draft_output,
                               args.review_intervals_output) if p is not None]
        input_paths = [args.srt, args.reference_txt, args.provider_words_json]
        input_set = {p.resolve() for p in input_paths if p is not None}
        if len({p.resolve() for p in outputs}) != len(outputs):
            raise ValueError("output paths overlap")
        if any(p.resolve() in input_set or p.exists() for p in outputs):
            raise ValueError("refusing input overwrite or existing output")
        srt_text = args.srt.read_text(encoding="utf-8-sig")
        reference = (args.reference_txt.read_text(encoding="utf-8-sig")
                     if args.reference_txt else None)
        report, draft = inspect(srt_text, reference=reference,
                                draft_merge_short=args.draft_output is not None)
        review_manifest = None
        if args.review_intervals_output is not None:
            evidence = (json.loads(args.provider_words_json.read_text(encoding="utf-8"))
                        if args.provider_words_json else None)
            review_manifest = build_review_interval_manifest(
                parse_srt(srt_text), provider_evidence=evidence,
                expected_source_sha=args.expected_source_sha,
                audio_duration_ms=args.audio_duration_ms)
        if args.report_output is not None:
            _write_exclusive(args.report_output, json.dumps(
                report, ensure_ascii=False, indent=2) + "\n", input_file=args.srt)
        if args.draft_output is not None:
            _write_exclusive(args.draft_output, draft or "", input_file=args.srt)
        if args.review_intervals_output is not None:
            _write_exclusive(args.review_intervals_output, json.dumps(
                review_manifest, ensure_ascii=False, indent=2) + "\n", input_file=args.srt)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        # No untrusted transcript text or secrets in error responses.
        print(json.dumps({"status": "INVALID_OR_UNSAFE_INPUT",
                          "error_type": type(exc).__name__,
                          "golden_qa_pass": False, "publication_authorized": False}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A format check or a partial merge NEVER authorizes Golden delivery.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
