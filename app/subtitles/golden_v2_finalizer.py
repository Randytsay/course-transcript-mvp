"""V2 deterministic candidate SRT/TXT renderer from approved Chirp-timed cues.

This performs structural QA only. It MUST NOT set the Golden marker or publish
to Drive: coverage/VAD/content/full-lineage gates remain external prerequisites.
"""
from __future__ import annotations

import re
from typing import Any


def _timestamp(milliseconds: int) -> str:
    h, rest = divmod(milliseconds, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_checked_candidates(
    cues: list[dict[str, Any]], *, require_acoustic_evidence: bool = True
) -> dict[str, Any]:
    """Create both artifacts from exactly the same validated text source."""
    if not cues:
        raise ValueError("No subtitle cues")
    srt_blocks: list[str] = []
    text_lines: list[str] = []
    prior_end = 0
    observed_ids: set[str] = set()
    for index, cue in enumerate(cues, 1):
        segment_id = str(cue.get("segment_id") or "").strip()
        if not segment_id or segment_id in observed_ids:
            raise ValueError("Missing/duplicate source segment IDs")
        observed_ids.add(segment_id)
        if cue.get("timing_source") != "chirp_3":
            raise ValueError("Non-Chirp timestamp evidence cannot authorize SRT")
        if require_acoustic_evidence and not cue.get("acoustic_verified"):
            raise ValueError("Unverified acoustic segment")
        if cue.get("adjudication") != "APPROVED":
            raise ValueError("Unresolved text adjudication")
        if type(cue.get("start_ms")) is not int or type(cue.get("end_ms")) is not int:
            raise ValueError("Subtitle timestamps must be integer milliseconds")
        start, end = cue["start_ms"], cue["end_ms"]
        if start < prior_end or start < 0 or end <= start:
            raise ValueError("Invalid/overlapping Chirp boundaries")
        prior_end = end
        text = str(cue.get("corrected_text") or "").strip()
        if not text or "\r" in text or "\n" in text or re.search(r"\s{2,}", text):
            raise ValueError("Empty or noncanonical cue text")
        srt_blocks.append(
            f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}"
        )
        text_lines.append(text)
    srt = "\n\n".join(srt_blocks) + "\n"
    txt = "\n".join(text_lines) + "\n"
    return {
        "srt": srt,
        "transcript_txt": txt,
        "cue_count": len(srt_blocks),
        "timing_source": "chirp_3",
        "acoustic_evidence_required": require_acoustic_evidence,
        "qa_status": "STRUCTURAL_PASS_ONLY",
        "golden_pass": False,
    }
