"""Offline, auditable 2022 Chirp-first MANUAL reference reader.

This is a text comparison source, NEVER provider output or acoustic evidence.
The trusted operator must stage both fixed filenames, pin the original manual
SRT hash, identify a reviewer, and bind the normalized source SHA and job.
No files are written and no paid model/provider is used by this reader.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_audio_staging import file_sha256

_TIMESTAMP = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


def _milliseconds(values: tuple[str, ...]) -> int:
    hh, mm, ss, ms = map(int, values)
    if mm >= 60 or ss >= 60:
        raise V2Conflict("invalid reference SRT timestamp")
    return ((hh * 60 + mm) * 60 + ss) * 1000 + ms


def _reference_text_and_extent(raw: bytes, duration_ms: int) -> tuple[str, float, dict[str, int]]:
    try:
        srt = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise V2Conflict("reference SRT is not valid UTF-8") from exc
    blocks = re.split(r"\n\s*\n", srt.strip())
    lines: list[str] = []
    previous_end = 0
    first_start: int | None = None
    short_overlap_count = 0
    blank_cue_count = 0
    for index, block in enumerate(blocks, 1):
        rows = block.splitlines()
        if len(rows) < 2 or rows[0].strip() != str(index):
            raise V2Conflict("reference SRT numbering or cue body invalid")
        if len(rows) == 2:
            blank_cue_count += 1
        match = _TIMESTAMP.fullmatch(rows[1].strip())
        if match is None:
            raise V2Conflict("reference SRT contains invalid timecodes")
        start = _milliseconds(match.groups()[:4])
        end = _milliseconds(match.groups()[4:])
        if end <= start or end > duration_ms + 500:
            raise V2Conflict("reference SRT cue invalid or exceeds source duration")
        if start < previous_end:
            # The historical 20221217 manually corrected reference contains
            # one 120 ms overlap. Never use this SRT for timing: small edits
            # can still be a text reference, but record the defect for QA.
            if previous_end - start > 250:
                raise V2Conflict("manual reference has large cue overlap")
            short_overlap_count += 1
        if first_start is None:
            first_start = start
        previous_end = end
        lines.append("".join(row.strip() for row in rows[2:]))
    if blank_cue_count > max(20, len(blocks) // 100):
        raise V2Conflict("too many blank reference cues for whole-source comparison")
    text = "".join(lines)
    if not text.strip() or first_start is None or duration_ms <= 0:
        raise V2Conflict("reference SRT has no source-bound text")
    if first_start > max(15000, duration_ms // 50):
        raise V2Conflict("reference SRT has unreviewed leading extent")
    # Temporal extent only; NOT a claim that every audio word was reviewed.
    extent_ratio = min(1.0, previous_end / duration_ms)
    if extent_ratio < 0.98:
        raise V2Conflict("reference SRT does not span full source timeline")
    return text, extent_ratio, {
        "blank_cue_count": blank_cue_count,
        "short_overlap_count": short_overlap_count,
    }


def read_chirp_first_reference(
    *, job_dir: Path, job_id: str, source_sha: str, duration_ms: int,
) -> tuple[str, dict[str, Any]]:
    root = Path(job_dir) / "golden-v2"
    metadata = root / "chirp-first-reference.json"
    reference = root / "chirp-first-reference.srt"
    if metadata.is_symlink() or reference.is_symlink():
        raise V2Conflict("reference path cannot be a symlink")
    try:
        packet = json.loads(metadata.read_text(encoding="utf-8"))
        raw = reference.read_bytes()
    except (OSError, ValueError) as exc:
        raise V2Conflict("missing or invalid trusted Chirp-first reference snapshot") from exc
    if (not isinstance(packet, dict)
            or packet.get("schema") != "golden-v2-chirp-first-reference-v1"
            or packet.get("job_id") != job_id
            or packet.get("source_sha256") != source_sha
            or packet.get("reference_srt_sha256") != hashlib.sha256(raw).hexdigest()
            or not isinstance(packet.get("reviewer"), str)
            or not packet["reviewer"].strip()
            or not isinstance(packet.get("review_reference"), str)
            or not packet["review_reference"].strip()
            or not isinstance(packet.get("source_kind"), str)
            or packet["source_kind"] != "HUMAN_CORRECTED_SRT"):
        raise V2Conflict("Chirp-first reference lacks source-bound operator attestation")
    if file_sha256(Path(job_dir) / "normalized.flac") != source_sha:
        raise V2Conflict("Chirp-first reference normalized source SHA mismatch")
    text, extent, defects = _reference_text_and_extent(raw, duration_ms)
    return text, {
        "reference_format_issues": defects,
        "reference_requires_format_review": bool(
            defects["blank_cue_count"] or defects["short_overlap_count"]),
        "schema": "golden-v2-chirp-first-reference-v1",
        "reference_evidence_sha256": file_sha256(metadata),
        "reference_srt_sha256": packet["reference_srt_sha256"],
        "reference_timeline_extent_ratio": round(extent, 6),
        "reference_kind": "HUMAN_CORRECTED_SRT",
        "reference_is_acoustic_evidence": False,
        "review_reference": packet["review_reference"],
    }
