"""Private, OFFLINE human-listening decision ledger template for Golden V2.

Produces no audio, transcription or SRT. No cloud/network, Docker, Drive,
Ledger, model, inference or production side effects. A valid human review
is STILL NOT an automated Golden QA approval or publication authorization.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED = {"NOT_REVIEWED", "SPEECH_CONFIRMED", "NON_SPEECH_CONFIRMED", "UNCERTAIN"}


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def create_template(
    interval_manifest: dict,
    *,
    normalized_audio_sha256: str,
    clip_sha256: list[str],
) -> dict:
    """Create exactly one blank human judgement per SRT review interval.

    Source timing and clip hashes are asserted by the supplying *private*
    local packet and must separately be verified against actual audio bytes.
    """
    if interval_manifest.get("schema") != "golden-v2-srt-audio-review-intervals-v1":
        raise ValueError("wrong interval manifest schema")
    intervals = interval_manifest.get("intervals")
    if (not isinstance(intervals, list) or not 1 <= len(intervals) <= 25
            or interval_manifest.get("interval_count") != len(intervals)
            or not isinstance(clip_sha256, list)
            or len(clip_sha256) != len(intervals)
            or not _sha(normalized_audio_sha256)
            or not all(_sha(sha) for sha in clip_sha256)):
        raise ValueError("incomplete source, interval or clip identity")
    if interval_manifest.get("interpolation_performed") is not False or (
        interval_manifest.get("golden_qa_pass") is not False
    ):
        raise ValueError("source manifest is not a blocked review draft")
    audio_ms = interval_manifest.get("audio_duration_ms")
    if type(audio_ms) is not int or audio_ms <= 0:
        raise ValueError("verified source duration is required")
    rows = []
    last_gap_end = -1
    total = 0
    for i, item in enumerate(intervals, 1):
        if not isinstance(item, dict):
            raise ValueError("invalid interval")
        fields = ("cue_before", "cue_after", "review_start_ms", "review_end_ms",
                  "srt_gap_ms")
        if any(type(item.get(x)) is not int for x in fields):
            raise ValueError("missing integer boundaries")
        start, end = item["review_start_ms"], item["review_end_ms"]
        gap_ms = item["srt_gap_ms"]
        # The supplied start/end clocks are for *padded* listening; exact gap
        # clocks are separately present and never overwritten from a review.
        if (not 0 <= start < end <= audio_ms or gap_ms < 8000
                or item["cue_before"] < 1
                or item["cue_after"] != item["cue_before"] + 1
                or item.get("review_duration_ms") != end - start
                or item.get("requires_audio_listening") is not True
                or item.get("text_alignment_repaired") is not False):
            raise ValueError("invalid bounded listening interval")
        if item["srt_gap_start"] >= item["srt_gap_end"]:
            raise ValueError("invalid SRT clock order")
        # Different padded listening clips may overlap, but the underlying
        # exact SRT gaps must be strictly increasing.
        if item["cue_before"] <= last_gap_end:
            raise ValueError("review intervals out of cue order")
        last_gap_end = item["cue_before"]
        total += gap_ms
        rows.append({
            "clip_id": f"clip_{i:02d}",
            "clip_sha256": clip_sha256[i - 1],
            "cue_before": item["cue_before"],
            "cue_after": item["cue_after"],
            "exact_srt_gap_start": item["srt_gap_start"],
            "exact_srt_gap_end": item["srt_gap_end"],
            "exact_srt_gap_ms": gap_ms,
            "listen_start_ms": start,
            "listen_end_ms": end,
            "speech_judgement": "NOT_REVIEWED",
            "reviewer_id": None,
            "reviewed_at_utc": None,
            "source_audio_listened": False,
            # Never include personal lecture text in GitHub; private
            # evidence references and hashes are sufficient for triage.
            "transcript_evidence_sha256": None,
            "speech_anchor_ranges_ms": [],
            "observation_private_reference": None,
            "review_notes": None,
        })
    if total != interval_manifest.get("total_srt_gap_ms"):
        raise ValueError("total SRT gap duration mismatch")
    return {
        "schema": "golden-v2-human-listening-adjudication-v1",
        "normalized_audio_sha256": normalized_audio_sha256,
        "audio_duration_ms": audio_ms,
        "clip_count": len(rows),
        "records": rows,
        "review_status": "AWAITING_HUMAN_REVIEW",
        "human_listening_complete": False,
        "audio_alignment_verified": False,
        "golden_qa_pass": False,
        "publication_authorized": False,
    }


def validate_review(candidate: dict, template: dict) -> dict:
    """Validate that a review was filled by a named person with bounded notes.

    No automatic Golden PASS on any path; sound perception is the *reviewer's*
    assertion and cannot be established by fields or ffprobe alone.
    """
    if any(candidate.get(key) != template[key] for key in
           ("schema", "normalized_audio_sha256", "audio_duration_ms",
            "clip_count")):
        raise ValueError("review does not belong to this source/packet")
    for gate in ("golden_qa_pass", "publication_authorized",
                 "audio_alignment_verified", "human_listening_complete"):
        if candidate.get(gate) is not False:
            raise ValueError("human review may not preapprove release")
    records = candidate.get("records")
    expected = template["records"]
    if not isinstance(records, list) or len(records) != len(expected):
        raise ValueError("record count changed")
    reviewed_count = 0
    open_count = 0
    for entry, original in zip(records, expected):
        if not isinstance(entry, dict):
            raise ValueError("invalid review row")
        immutable = (
            "clip_id", "clip_sha256", "cue_before", "cue_after",
            "exact_srt_gap_start", "exact_srt_gap_end", "exact_srt_gap_ms",
            "listen_start_ms", "listen_end_ms",
        )
        if any(entry.get(k) != original[k] for k in immutable):
            raise ValueError("source timeline or clip identity was edited")
        judgement = entry.get("speech_judgement")
        if judgement not in _ALLOWED:
            raise ValueError("unrecognized human judgement")
        if judgement == "NOT_REVIEWED":
            # No person or timing has been invented for unreviewed rows.
            if (entry.get("source_audio_listened") is not False
                or entry.get("reviewer_id") is not None
                or entry.get("reviewed_at_utc") is not None
                or entry.get("speech_anchor_ranges_ms") != []):
                raise ValueError("unreviewed item has fabricated review evidence")
            open_count += 1
            continue
        reviewer, time = entry.get("reviewer_id"), entry.get("reviewed_at_utc")
        if (not isinstance(reviewer, str) or not 1 <= len(reviewer) <= 80
                or not isinstance(time, str)
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", time)
                or entry.get("source_audio_listened") is not True):
            raise ValueError("reviewer identity, UTC time and actual listening required")
        reviewed_count += 1
        spans = entry.get("speech_anchor_ranges_ms")
        if not isinstance(spans, list) or len(spans) > 1000:
            raise ValueError("invalid bounded anchors")
        last_end = -1
        for span in spans:
            if (not isinstance(span, dict)
                    or type(span.get("start_ms")) is not int
                    or type(span.get("end_ms")) is not int
                    or not entry["listen_start_ms"] <= span["start_ms"] < span["end_ms"] <= entry["listen_end_ms"]
                    or span["start_ms"] < last_end):
                raise ValueError("human-timed span falls outside listened audio or overlaps")
            last_end = span["end_ms"]
        if judgement == "SPEECH_CONFIRMED":
            if not _sha(entry.get("transcript_evidence_sha256")) or not spans:
                raise ValueError("speech requires private transcript hash and timed evidence")
        elif judgement == "NON_SPEECH_CONFIRMED":
            if spans or entry.get("transcript_evidence_sha256") is not None:
                raise ValueError("non-speech must not carry invented transcript anchors")
        else:
            open_count += 1
    return {
        "schema": "golden-v2-human-listening-validation-v1",
        "reviewed_count": reviewed_count,
        "open_or_uncertain_count": open_count,
        "all_intervals_reviewed": reviewed_count == len(records),
        "review_status": (
            "HUMAN_ANNOTATIONS_COMPLETE_NEEDS_QA"
            if reviewed_count == len(records) and open_count == 0
            else "HUMAN_REVIEW_INCOMPLETE"
        ),
        # A human report is still NOT acoustic QA, SRT correction or delivery.
        "audio_alignment_verified": False,
        "golden_qa_pass": False,
        "publication_authorized": False,
    }


def _exclusive_write(path: Path, data: object, protected: list[Path]) -> None:
    if path.resolve() in {p.resolve() for p in protected}:
        raise ValueError("refusing input overwrite")
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-manifest", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--clip-shas-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.interval_manifest.read_text(encoding="utf-8"))
        clip_hashes = json.loads(args.clip_shas_json.read_text(encoding="utf-8"))
        output = create_template(
            manifest, normalized_audio_sha256=args.source_sha256,
            clip_sha256=clip_hashes,
        )
        _exclusive_write(args.output, output, [
            args.interval_manifest, args.clip_shas_json,
        ])
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        print(json.dumps({"status": "INVALID_OR_UNSAFE_INPUT",
                          "error_type": type(exc).__name__,
                          "golden_qa_pass": False,
                          "publication_authorized": False}))
        return 2
    print(json.dumps({
        "status": "AWAITING_HUMAN_REVIEW",
        "clip_count": output["clip_count"],
        "golden_qa_pass": False,
        "publication_authorized": False,
    }))
    return 2  # Nonzero exit by design; never use as a Golden-PASS gate.


if __name__ == "__main__":
    raise SystemExit(main())
