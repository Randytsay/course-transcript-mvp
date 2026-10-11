"""Provider-free conservative acoustic check for immutable V2 Chirp evidence.

An empty-looking timestamp interval is a candidate, not proof of omitted speech.
Only local audio inspection can dismiss it. This module never submits repairs.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_audio_staging import duration_seconds, file_sha256
from app.providers.chirp_completeness_gate import _audible, _vad_assessment


def _wire(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _peak_db(job_dir: Path, start: int, end: int) -> float | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-ss",
             f"{start / 1000:.3f}", "-i", str(job_dir / "normalized.flac"),
             "-t", f"{(end-start)/1000:.3f}", "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode:
            return None
        match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf)) dB", result.stderr)
        if match:
            return float(match.group(1))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def inspect_v2_acoustic(
    *, job_dir: Path, merged: dict[str, Any],
    gap_min_ms: int = 3000, tail_min_ms: int = 2000,
    max_repair_intervals: int = 12, max_repair_total_ms: int = 600_000,
    word_boundary_grace_ms: int = 200,
) -> dict[str, Any]:
    """Validate source and Chirp timebase and flag only audibly suspicious gaps.

    A quiet interval passes only with near-digital-silence peak (< -65dBFS) or
    local VAD agreement on nonspeech. Unavailable probes remain REVIEW.
    """
    if (not 0 < gap_min_ms <= 30_000 or not 0 < tail_min_ms <= 30_000
            or not 0 < max_repair_intervals <= 100
            or not 0 < max_repair_total_ms <= 3_600_000
            or not 0 <= word_boundary_grace_ms <= 500):
        raise V2Conflict("invalid bounded acoustic QA settings")
    audio = Path(job_dir) / "normalized.flac"
    if (not audio.is_file() or merged.get("timing_authority") != "chirp_3"
            or merged.get("source_sha") != file_sha256(audio)):
        raise V2Conflict("acoustic QA requires matching immutable Chirp/FLAC provenance")
    duration = round(duration_seconds(audio) * 1000)
    if type(merged.get("duration_ms")) is not int or abs(duration - merged["duration_ms"]) > 250:
        raise V2Conflict("acoustic duration mismatch")
    words = merged.get("words")
    if not isinstance(words, list) or not words:
        raise V2Conflict("no Chirp word timing evidence")
    previous_end = 0
    gaps: list[tuple[int, int, str]] = []
    for index, word in enumerate(words):
        start, end = word.get("start_ms"), word.get("end_ms")
        if (type(start) is not int or type(end) is not int or start < previous_end
                or end <= start or end > duration + 250 or not str(word.get("word") or "").strip()):
            raise V2Conflict("invalid or overlapping Chirp word boundary")
        threshold = tail_min_ms if index == 0 else gap_min_ms
        if start - previous_end >= threshold:
            gaps.append((previous_end, start, "LEADING" if index == 0 else "INTERIOR"))
        previous_end = end
    if duration - previous_end >= tail_min_ms:
        gaps.append((previous_end, duration, "TAIL"))

    intervals: list[dict[str, Any]] = []
    for start, end, position in gaps:
        # Chirp offsets are approximate at phoneme edges. Treat at most 200 ms
        # adjacent to recognized words as part of the word, not missing speech.
        # Inspect the remaining interval; a genuine multi-second omission still
        # blocks publication. Never trim a gap that contains no recognized edge.
        probe_start = start + (word_boundary_grace_ms if position != "LEADING" else 0)
        probe_end = end - (word_boundary_grace_ms if position != "TAIL" else 0)
        if probe_end <= probe_start:
            raise V2Conflict("acoustic gap smaller than allowed probe window")
        audible = _audible(Path(job_dir), probe_start, probe_end)
        vad = _vad_assessment(Path(job_dir), probe_start, probe_end)
        peak_db = _peak_db(Path(job_dir), probe_start, probe_end)
        # Near-digital silence is stronger evidence than an RMS threshold;
        # audible nonspeech with VAD uncertainty remains in review.
        strong_silence = peak_db is not None and peak_db <= -65
        non_speech = strong_silence or bool(vad and vad.get("robust_non_speech"))
        interval = {
            "start_ms": start, "end_ms": end, "position": position,
            "acoustic_probe_start_ms": probe_start,
            "acoustic_probe_end_ms": probe_end,
            "audible_above_rms_threshold": audible,
            "max_volume_dbfs": peak_db,
            "vad": vad, "disposition": "NONSPEECH_CONFIRMED" if non_speech else "REVIEW",
            "reason": "non_speech_evidence" if non_speech else "possible_audible_untranscribed_audio",
        }
        intervals.append(interval)

    blockers = [interval for interval in intervals if interval["disposition"] == "REVIEW"]
    repair_intervals: list[dict[str, Any]] = []
    total = 0
    for gap in blockers:
        if len(repair_intervals) >= max_repair_intervals:
            break
        # Proposal only, paid Standard Batch requires separate source/cost approval.
        start = max(0, gap["start_ms"] - 2500)
        end = min(duration, gap["end_ms"] + 2500, start + 300_000)
        if total + end - start > max_repair_total_ms:
            continue
        repair_intervals.append({"start_ms": start, "end_ms": end,
                                 "strategy": "STANDARD_BATCH", "submit_authorized": False})
        total += end - start
    return {
        "schema": "golden-v2-acoustic-qa-v1", "source_sha256": merged["source_sha"],
        "merged_sha256": hashlib.sha256(_wire(merged)).hexdigest(),
        "duration_ms": duration, "word_count": len(words),
        "status": "PASS" if not blockers else "REVIEW",
        "gaps_examined": intervals, "blocker_count": len(blockers),
        "repair_plan": {"strategy": "STANDARD_BATCH", "submit_authorized": False,
                        "status": "PROPOSED_REQUIRES_APPROVAL" if repair_intervals else "NONE",
                        "intervals": repair_intervals, "total_duration_ms": total,
                        "truncated": len(repair_intervals) < len(blockers)},
        "local_only": True, "provider_calls_started": False,
    }
