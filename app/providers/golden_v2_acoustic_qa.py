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
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode:
            return None
        match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf)) dB", result.stderr)
        if match:
            peak = float(match.group(1))
            # ffmpeg reports -inf for digital silence. Keep the QA JSON
            # standards-compliant while preserving a strong silence signal.
            return -120.0 if peak == float("-inf") else peak
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _probe_nonspeech(job_dir: Path, start: int, end: int) -> dict[str, Any]:
    """Inspect one bounded, contiguous piece of an untranscribed interval."""
    try:
        audible = _audible(job_dir, start, end)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        audible = None
    try:
        vad = _vad_assessment(job_dir, start, end)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        vad = None
    peak_db = _peak_db(job_dir, start, end)
    strong_silence = peak_db is not None and peak_db <= -65
    non_speech = strong_silence or bool(vad and vad.get("robust_non_speech"))
    speech = (not non_speech and audible is True and bool(vad)
              and vad.get("robust_speech") is True)
    return {
        "start_ms": start, "end_ms": end,
        "audible_above_rms_threshold": audible, "max_volume_dbfs": peak_db,
        "vad": vad,
        "disposition": ("NONSPEECH_CONFIRMED" if non_speech else
                        "SPEECH_CONFIRMED" if speech else "REVIEW"),
    }


def inspect_v2_acoustic(
    *, job_dir: Path, merged: dict[str, Any],
    gap_min_ms: int = 3000, tail_min_ms: int = 2000,
    max_repair_intervals: int = 12, max_repair_total_ms: int = 600_000,
    word_boundary_grace_ms: int = 200, max_gaps_to_probe: int = 16,
    max_gap_probe_ms: int = 30_000, max_total_probe_slices: int = 48,
) -> dict[str, Any]:
    """Validate source and Chirp timebase and flag only audibly suspicious gaps.

    A quiet interval passes only with near-digital-silence peak (< -65dBFS) or
    local VAD agreement on nonspeech. Unavailable probes remain REVIEW.
    """
    if (not 0 < gap_min_ms <= 30_000 or not 0 < tail_min_ms <= 30_000
            or not 0 < max_repair_intervals <= 100
            or not 0 < max_repair_total_ms <= 3_600_000
            or not 0 <= word_boundary_grace_ms <= 500
            or type(max_gaps_to_probe) is not int or not 1 <= max_gaps_to_probe <= 64
            or type(max_gap_probe_ms) is not int or not 1000 <= max_gap_probe_ms <= 120_000
            or type(max_total_probe_slices) is not int
            or not 1 <= max_total_probe_slices <= 256):
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
    slices_used = 0
    probed = gaps[:max_gaps_to_probe]
    unprobed = gaps[max_gaps_to_probe:]
    for start, end, position in probed:
        # Chirp offsets are approximate at phoneme edges. Treat at most 200 ms
        # adjacent to recognized words as part of the word, not missing speech.
        # Inspect the remaining interval; a genuine multi-second omission still
        # blocks publication. Never trim a gap that contains no recognized edge.
        probe_start = start + (word_boundary_grace_ms if position != "LEADING" else 0)
        probe_end = end - (word_boundary_grace_ms if position != "TAIL" else 0)
        if probe_end <= probe_start:
            raise V2Conflict("acoustic gap smaller than allowed probe window")
        required_slices = (probe_end - probe_start + max_gap_probe_ms - 1) // max_gap_probe_ms
        if slices_used + required_slices > max_total_probe_slices:
            intervals.append({
                "start_ms": start, "end_ms": end, "position": position,
                "acoustic_probe_start_ms": probe_start,
                "acoustic_probe_end_ms": probe_end,
                "audible_above_rms_threshold": None, "max_volume_dbfs": None,
                "vad": None, "disposition": "REVIEW",
                "reason": "acoustic_gap_probe_budget_exceeded",
            })
            continue
        probes = []
        cursor = probe_start
        while cursor < probe_end:
            following = min(probe_end, cursor + max_gap_probe_ms)
            probes.append(_probe_nonspeech(Path(job_dir), cursor, following))
            cursor = following
        slices_used += len(probes)
        # Every contiguous slice must have independent nonspeech evidence.
        # One speech or uncertain slice blocks the complete gap.
        non_speech = all(p["disposition"] == "NONSPEECH_CONFIRMED" for p in probes)
        speech = any(p["disposition"] == "SPEECH_CONFIRMED" for p in probes)
        disposition = (
            "NONSPEECH_CONFIRMED" if non_speech else
            "SPEECH_CONFIRMED" if speech else "REVIEW"
        )
        peak_values = [p["max_volume_dbfs"] for p in probes if p["max_volume_dbfs"] is not None]
        interval = {
            "start_ms": start, "end_ms": end, "position": position,
            "acoustic_probe_start_ms": probe_start,
            "acoustic_probe_end_ms": probe_end,
            "audible_above_rms_threshold": (
                True if any(p["audible_above_rms_threshold"] is True for p in probes)
                else False if all(p["audible_above_rms_threshold"] is False for p in probes)
                else None),
            "max_volume_dbfs": max(peak_values) if peak_values else None,
            "vad": probes[0]["vad"] if len(probes) == 1 else None,
            "probe_slices": probes, "disposition": disposition,
            "reason": ("segmented_non_speech_evidence" if non_speech and len(probes) > 1 else
                       "non_speech_evidence" if non_speech else
                       "positive_speech_evidence" if speech else
                       "audio_character_uncertain"),
        }
        intervals.append(interval)
    for start, end, position in unprobed:
        intervals.append({
            "start_ms": start, "end_ms": end, "position": position,
            "acoustic_probe_start_ms": None, "acoustic_probe_end_ms": None,
            "audible_above_rms_threshold": None, "max_volume_dbfs": None,
            "vad": None, "disposition": "REVIEW",
            "reason": "acoustic_gap_probe_limit_reached",
        })

    blockers = [interval for interval in intervals
                if interval["disposition"] != "NONSPEECH_CONFIRMED"]
    speech_gaps = [interval for interval in intervals
                   if interval["disposition"] == "SPEECH_CONFIRMED"]
    repair_intervals: list[dict[str, Any]] = []
    total = 0
    for gap_index, gap in enumerate(speech_gaps):
        cursor = gap["start_ms"]
        while cursor < gap["end_ms"]:
            if len(repair_intervals) >= max_repair_intervals:
                break
            # Leave room for 2.5 seconds of audio context on both sides while
            # staying inside Speech Batch's 5-minute per-file ceiling.
            target_end = min(gap["end_ms"], cursor + 295_000)
            start = max(0, cursor - 2500)
            end = min(duration, target_end + 2500)
            if total + end - start > max_repair_total_ms:
                break
            repair_intervals.append({
                "start_ms": start, "end_ms": end,
                "target_intervals": [{"start_ms": cursor, "end_ms": target_end,
                                      "gap_index": gap_index}],
                "strategy": "STANDARD_BATCH", "submit_authorized": False,
            })
            total += end - start
            cursor = target_end
        if cursor < gap["end_ms"]:
            break
    nonspeech_count = sum(i["disposition"] == "NONSPEECH_CONFIRMED" for i in intervals)
    uncertain_count = sum(i["disposition"] == "REVIEW" for i in intervals)
    repair_truncated = len(repair_intervals) < len(speech_gaps)
    return {
        "schema": "golden-v2-acoustic-qa-v1", "source_sha256": merged["source_sha"],
        "merged_sha256": hashlib.sha256(_wire(merged)).hexdigest(),
        "duration_ms": duration, "word_count": len(words),
        "status": "PASS" if not blockers else "REVIEW",
        "gaps_examined": intervals, "blocker_count": len(blockers),
        "nonspeech_confirmed_count": nonspeech_count,
        "speech_confirmed_count": len(speech_gaps),
        "uncertain_count": uncertain_count,
        "unexamined_gap_count": len(unprobed),
        "acoustic_probe_slices_used": slices_used,
        "repair_plan": {"strategy": "STANDARD_BATCH", "submit_authorized": False,
                        "status": "PROPOSED_REQUIRES_APPROVAL" if repair_intervals else "NONE",
                        "intervals": repair_intervals, "total_duration_ms": total,
                        "truncated": repair_truncated,
                        "manual_review_required": uncertain_count > 0 or repair_truncated},
        "local_only": True, "provider_calls_started": False,
    }
