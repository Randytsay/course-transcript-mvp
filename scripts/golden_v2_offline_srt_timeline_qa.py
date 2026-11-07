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
    args = parser.parse_args()
    try:
        srt_text = args.srt.read_text(encoding="utf-8-sig")
        reference = (args.reference_txt.read_text(encoding="utf-8-sig")
                     if args.reference_txt else None)
        report, draft = inspect(srt_text, reference=reference,
                                draft_merge_short=args.draft_output is not None)
        if args.report_output is not None:
            _write_exclusive(args.report_output, json.dumps(
                report, ensure_ascii=False, indent=2) + "\n", input_file=args.srt)
        if args.draft_output is not None:
            if args.report_output is not None and args.draft_output.resolve() == args.report_output.resolve():
                raise ValueError("report and draft output must differ")
            _write_exclusive(args.draft_output, draft or "", input_file=args.srt)
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
