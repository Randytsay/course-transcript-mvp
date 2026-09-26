"""Pre-handoff completeness gate for Chirp 3 evidence.

This gate runs before any ChatGPT handoff. It is provider-free: it only reads
existing Chirp artifacts and local audio, then blocks handoff when ASR
completeness is materially uncertain.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.providers.qa_report import base_chunk_density_reports, density_windows, tail_coverage_assessment


DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "")
JOB = DATA_DIR / "jobs" / JOB_NAME
REPORT = "chirp-completeness.json"
REPAIR_PLAN = "chirp-completeness-repair-plan.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _audio_ms(job_dir: Path) -> int:
    plan = _read_json(job_dir / "chunk-plan.json", {})
    try:
        return round(float(plan["duration_seconds"]) * 1000)
    except (KeyError, TypeError, ValueError):
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(job_dir / "normalized.flac"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return round(float(result.stdout.strip()) * 1000)


def _audible(job_dir: Path, start_ms: int, end_ms: int) -> bool | None:
    if end_ms <= start_ms:
        return False
    source = job_dir / "normalized.flac"
    if not source.is_file():
        return None
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-ss", f"{start_ms / 1000:.3f}",
            "-i", str(source),
            "-t", f"{(end_ms - start_ms) / 1000:.3f}",
            "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
    if not match:
        return None
    threshold = float(os.environ.get("CHIRP_SPEECH_MEAN_VOLUME_DB", "-50"))
    return float(match.group(1)) > threshold


def _vad_assessment(job_dir: Path, start_ms: int, end_ms: int) -> dict[str, Any] | None:
    """Return conservative local speech evidence for an audible subtitle gap.

    This is intentionally fail-closed: missing dependencies, ffmpeg failures,
    short/invalid PCM, or VAD errors return ``None`` so the existing audible
    gap remains blocking. A gap is downgraded only when both permissive and
    aggressive WebRTC VAD modes agree that speech occupancy is very low and
    the RMS level is also low.
    """
    if end_ms <= start_ms:
        return None
    source = job_dir / "normalized.flac"
    if not source.is_file():
        return None
    try:
        import webrtcvad
    except ImportError:
        return None

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_ms / 1000:.3f}",
            "-i", str(source),
            "-t", f"{(end_ms - start_ms) / 1000:.3f}",
            "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
        ],
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0 or not result.stdout:
        return None

    frame_ms = 30
    sample_rate = 16_000
    frame_bytes = sample_rate * frame_ms // 1000 * 2
    frame_count = len(result.stdout) // frame_bytes
    if frame_count < 10:
        return None
    pcm = result.stdout[: frame_count * frame_bytes]
    try:
        vad0 = webrtcvad.Vad(0)
        vad3 = webrtcvad.Vad(3)
        speech0 = 0
        speech3 = 0
        for index in range(frame_count):
            frame = pcm[index * frame_bytes : (index + 1) * frame_bytes]
            speech0 += int(vad0.is_speech(frame, sample_rate))
            speech3 += int(vad3.is_speech(frame, sample_rate))
    except Exception:
        return None

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return None
    mean_square = sum(int(sample) * int(sample) for sample in samples) / len(samples)
    rms = math.sqrt(mean_square)
    rms_dbfs = 20 * math.log10(rms / 32768) if rms > 0 else -120.0

    mode0_ratio = speech0 / frame_count
    mode3_ratio = speech3 / frame_count
    mode0_max = float(os.environ.get("CHIRP_VAD_NONSPEECH_MODE0_MAX", "0.15"))
    mode3_max = float(os.environ.get("CHIRP_VAD_NONSPEECH_MODE3_MAX", "0.08"))
    rms_max_dbfs = float(os.environ.get("CHIRP_VAD_NONSPEECH_RMS_MAX_DBFS", "-35"))
    robust_non_speech = (
        mode0_ratio < mode0_max
        and mode3_ratio < mode3_max
        and rms_dbfs <= rms_max_dbfs
    )
    return {
        "engine": "webrtcvad",
        "sample_rate_hz": sample_rate,
        "frame_ms": frame_ms,
        "frame_count": frame_count,
        "mode0_speech_ratio": round(mode0_ratio, 4),
        "mode3_speech_ratio": round(mode3_ratio, 4),
        "rms_dbfs": round(rms_dbfs, 2),
        "thresholds": {
            "mode0_max": mode0_max,
            "mode3_max": mode3_max,
            "rms_max_dbfs": rms_max_dbfs,
        },
        "robust_non_speech": robust_non_speech,
    }


def _verified_nonlexical_windows(job_dir: Path) -> list[dict[str, int]]:
    pairs: list[tuple[Path, Path]] = [
        (
            job_dir / "chirp-targeted-patch-plan.json",
            job_dir / "chirp-targeted-patch-complete.json",
        )
    ]
    archive_root = job_dir / "targeted-patch-archives"
    if archive_root.is_dir():
        for complete_path in archive_root.rglob("chirp-targeted-patch-complete.json"):
            pairs.append(
                (
                    complete_path.parent / "chirp-targeted-patch-plan.json",
                    complete_path,
                )
            )

    windows: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for plan_path, complete_path in pairs:
        plan = _read_json(plan_path, {})
        complete = _read_json(complete_path, {})
        items = {
            int(item["patch_index"]): item
            for item in plan.get("items", [])
            if isinstance(item, dict) and item.get("patch_index") is not None
        } if isinstance(plan, dict) else {}
        if not isinstance(complete, dict):
            continue
        for verdict in complete.get("verdicts", []):
            if not isinstance(verdict, dict):
                continue
            try:
                patch_index = int(verdict["patch_index"])
                gap_words = int(verdict.get("target_gap_word_count") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            item = items.get(patch_index)
            if not item or gap_words != 0 or not verdict.get("operation_name"):
                continue
            start_ms = int(item.get("gap_start_ms", item.get("source_start_ms", 0)))
            end_ms = int(item.get("gap_end_ms", item.get("source_end_ms", 0)))
            key = (start_ms, end_ms)
            if end_ms <= start_ms or key in seen:
                continue
            seen.add(key)
            windows.append({"start_ms": start_ms, "end_ms": end_ms})
    return windows


def build_auto_repair_patch_plan(
    job_dir: Path,
    report: dict[str, Any],
    *,
    context_ms: int = 5_000,
    merge_gap_ms: int = 1_000,
    max_total_ms: int = 600_000,
    max_patches: int = 12,
    patch_index_base: int = 920_000,
) -> dict[str, Any]:
    """Build one bounded paid repair round from completeness blockers.

    The worker may call this repeatedly across bounded convergence rounds. Each
    round receives a distinct patch_index_base so retained patch artifacts are
    never confused with a later residual repair.

    This never submits a provider request. The worker still applies the normal
    reserved-budget gate before any Dynamic Batch operation is started.
    """
    chunk_plan = _read_json(job_dir / "chunk-plan.json", {})
    chunks = [
        item
        for item in (chunk_plan.get("chunks", []) if isinstance(chunk_plan, dict) else [])
        if isinstance(item, dict)
    ]
    blockers = report.get("blockers", []) if isinstance(report, dict) else []
    if not isinstance(blockers, list) or not blockers:
        return {
            "version": "targeted-patch-v1",
            "policy": "chirp_completeness_auto_repair_v1",
            "status": "none",
            "items": [],
            "total_duration_ms": 0,
        }

    candidates: list[dict[str, Any]] = []
    repairable = {
        "audible_subtitle_gap",
        "uncovered_audio_tail",
        "short_audible_tail_requires_review",
    }
    for blocker in blockers:
        if not isinstance(blocker, dict) or blocker.get("reason") not in repairable:
            continue
        reason = str(blocker["reason"])
        if reason == "audible_subtitle_gap":
            gap_start = int(blocker.get("gap_start_ms", 0))
            gap_end = int(blocker.get("gap_end_ms", 0))
        else:
            gap_start = int(blocker.get("start_ms", 0))
            gap_end = int(blocker.get("end_ms", 0))
        if gap_end <= gap_start:
            continue
        midpoint = (gap_start + gap_end) // 2
        parent = next(
            (
                chunk
                for chunk in chunks
                if int(chunk.get("source_start_ms", 0))
                <= midpoint
                < int(chunk.get("source_end_ms", 0))
            ),
            None,
        )
        if parent is None and reason in {
            "uncovered_audio_tail",
            "short_audible_tail_requires_review",
        }:
            parent = next(
                (
                    chunk
                    for chunk in reversed(chunks)
                    if int(chunk.get("source_end_ms", 0)) > gap_start
                ),
                chunks[-1] if chunks else None,
            )
        if parent is None:
            continue
        parent_start = int(parent.get("source_start_ms", 0))
        parent_end = int(parent.get("source_end_ms", 0))
        if reason in {
            "uncovered_audio_tail",
            "short_audible_tail_requires_review",
        }:
            # The completeness blocker already uses the true media end as
            # gap_end.  Do not clip the repair to the final base chunk; doing
            # so is what left 10-15 second residual tails after round one.
            source_start = max(0, gap_start - context_ms)
            source_end = gap_end
        else:
            source_start = max(parent_start, gap_start - context_ms)
            source_end = min(parent_end, gap_end + context_ms)
        if source_end <= source_start:
            continue
        candidates.append(
            {
                "parent_chunk_index": int(parent["chunk_index"]),
                "source_start_ms": source_start,
                "source_end_ms": source_end,
                "gap_start_ms": gap_start,
                "gap_end_ms": gap_end,
                "reasons": [reason],
            }
        )

    candidates.sort(
        key=lambda item: (
            int(item["parent_chunk_index"]),
            int(item["source_start_ms"]),
            int(item["source_end_ms"]),
        )
    )
    merged: list[dict[str, Any]] = []
    for item in candidates:
        if (
            merged
            and merged[-1]["parent_chunk_index"] == item["parent_chunk_index"]
            and int(item["source_start_ms"]) <= int(merged[-1]["source_end_ms"]) + merge_gap_ms
        ):
            merged[-1]["source_end_ms"] = max(
                int(merged[-1]["source_end_ms"]),
                int(item["source_end_ms"]),
            )
            merged[-1]["gap_start_ms"] = min(
                int(merged[-1]["gap_start_ms"]),
                int(item["gap_start_ms"]),
            )
            merged[-1]["gap_end_ms"] = max(
                int(merged[-1]["gap_end_ms"]),
                int(item["gap_end_ms"]),
            )
            merged[-1]["reasons"].extend(item["reasons"])
        else:
            merged.append(dict(item))

    items: list[dict[str, Any]] = []
    for offset, item in enumerate(merged, start=1):
        source_start = int(item["source_start_ms"])
        source_end = int(item["source_end_ms"])
        gap_start = int(item["gap_start_ms"])
        gap_end = int(item["gap_end_ms"])
        items.append(
            {
                "patch_index": patch_index_base + offset,
                "parent_chunk_index": int(item["parent_chunk_index"]),
                "source_start_ms": source_start,
                "source_end_ms": source_end,
                "duration_ms": source_end - source_start,
                "gap_start_ms": gap_start,
                "gap_end_ms": gap_end,
                "gap_ms": gap_end - gap_start,
                "role": "patch",
                "patch_mode": "replace_window",
                "reason": "+".join(sorted(set(item["reasons"]))),
                "automatic_paid_retry": True,
            }
        )

    total_duration_ms = sum(int(item["duration_ms"]) for item in items)
    blocked_reason = None
    if len(items) > max_patches:
        blocked_reason = "patch_count_cap_exceeded"
    elif total_duration_ms > max_total_ms:
        blocked_reason = "repair_duration_cap_exceeded"

    return {
        "version": "targeted-patch-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "job": job_dir.name,
        "policy": "chirp_completeness_auto_repair_v1",
        "status": "blocked" if blocked_reason else ("planned" if items else "none"),
        "items": [] if blocked_reason else items,
        "proposed_patch_count": len(items),
        "total_duration_ms": total_duration_ms,
        "max_total_duration_ms": max_total_ms,
        "max_patch_count": max_patches,
        "auto_submit_blocked_reason": blocked_reason,
    }


def _covered_by_verified_nonlexical(start_ms: int, end_ms: int, windows: list[dict[str, int]]) -> bool:
    duration = max(1, end_ms - start_ms)
    for window in windows:
        overlap = max(0, min(end_ms, window["end_ms"]) - max(start_ms, window["start_ms"]))
        if overlap / duration >= 0.80:
            return True
    return False


def _targeted_patch_zero_word_evidence(
    job_dir: Path,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any] | None:
    """Return completed patch evidence when the *current* residual window has no words.

    The completeness gap can shift after a patch rebuilds subtitles, so the
    original target_gap_word_count is not sufficient. This checks the actual
    retained patch word timeline against the current gap/tail.
    """
    if end_ms <= start_ms:
        return None
    pairs: list[tuple[Path, Path]] = [
        (
            job_dir / "chirp-targeted-patch-plan.json",
            job_dir / "chirp-targeted-patch-complete.json",
        )
    ]
    archive_root = job_dir / "targeted-patch-archives"
    if archive_root.is_dir():
        for complete_path in archive_root.rglob("chirp-targeted-patch-complete.json"):
            pairs.append(
                (
                    complete_path.parent / "chirp-targeted-patch-plan.json",
                    complete_path,
                )
            )

    for plan_path, complete_path in pairs:
        plan = _read_json(plan_path, {})
        complete = _read_json(complete_path, {})
        if not isinstance(plan, dict) or not isinstance(complete, dict):
            continue
        items = {
            int(item["patch_index"]): item
            for item in plan.get("items", [])
            if isinstance(item, dict) and item.get("patch_index") is not None
        }
        for verdict in complete.get("verdicts", []):
            if not isinstance(verdict, dict) or not verdict.get("operation_name"):
                continue
            try:
                patch_index = int(verdict["patch_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if str(verdict.get("status") or "") != "SUCCEEDED":
                continue
            item = items.get(patch_index)
            if not item:
                continue
            source_start = int(item.get("source_start_ms", 0))
            source_end = int(item.get("source_end_ms", 0))
            if source_start > start_ms or source_end < end_ms:
                continue

            words_payload = _read_json(
                job_dir / "chunks" / f"chunk-{patch_index:03d}" / "words.json",
                {},
            )
            words = (
                words_payload.get("words", [])
                if isinstance(words_payload, dict)
                else []
            )
            if not isinstance(words, list):
                continue
            overlapping = 0
            for word in words:
                if not isinstance(word, dict):
                    continue
                word_start = int(word.get("start_ms", 0))
                word_end = int(word.get("end_ms", 0))
                if word_end > word_start:
                    has_overlap = max(word_start, start_ms) < min(word_end, end_ms)
                else:
                    has_overlap = start_ms <= word_start < end_ms
                overlapping += int(has_overlap)
            if overlapping == 0:
                return {
                    "patch_index": patch_index,
                    "operation_name": verdict.get("operation_name"),
                    "source_start_ms": source_start,
                    "source_end_ms": source_end,
                    "residual_start_ms": start_ms,
                    "residual_end_ms": end_ms,
                    "overlapping_word_count": 0,
                }
    return None


def evaluate(job_dir: Path = JOB, *, audibility_probe=None, speech_probe=None) -> dict[str, Any]:
    raw = _read_json(job_dir / "subtitles.json", {})
    merged = _read_json(job_dir / "merged-words.json", {})
    segments = raw.get("segments", []) if isinstance(raw, dict) else []
    words = merged.get("words", []) if isinstance(merged, dict) else []
    if not isinstance(segments, list) or not isinstance(words, list) or not segments:
        raise RuntimeError("Chirp completeness gate requires subtitles.json and merged-words.json")

    audio_ms = _audio_ms(job_dir)
    probe = audibility_probe or (lambda start, end: _audible(job_dir, start, end))
    vad_probe = speech_probe or (lambda start, end: _vad_assessment(job_dir, start, end))
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    repair_items: list[dict[str, Any]] = []

    # Structural timing integrity.
    if any(int(word.get("end_ms", 0)) <= int(word.get("start_ms", 0)) for word in words if isinstance(word, dict)):
        blockers.append({"reason": "non_positive_word_duration"})
    if any(
        int(b.get("start_ms", 0)) < int(a.get("start_ms", 0))
        for a, b in zip(words, words[1:])
        if isinstance(a, dict) and isinstance(b, dict)
    ):
        blockers.append({"reason": "word_timeline_regression"})
    if any(int(seg.get("end_ms", 0)) <= int(seg.get("start_ms", 0)) for seg in segments if isinstance(seg, dict)):
        blockers.append({"reason": "non_positive_segment_duration"})

    # Course-relative density, with existing targeted-patch evidence taken into account.
    chunk_reports, chunk_plans = base_chunk_density_reports(
        job_dir, audio_ms, audibility_probe=probe
    )
    for item in chunk_plans:
        blocker = {"reason": "course_relative_chunk_density", **item}
        blockers.append(blocker)
        repair_items.append(blocker)
    for report in chunk_reports:
        if report.get("classification") == "targeted_patch_no_lexical_tokens":
            warnings.append(
                {
                    "reason": "verified_nonlexical_patch_window",
                    "chunk_index": report.get("chunk_index"),
                    "recommended_action": report.get("recommended_action"),
                }
            )

    density, density_plans = density_windows(segments, audio_ms)
    for item in density_plans:
        blocker = {"reason": "course_density_window", **item}
        blockers.append(blocker)
        repair_items.append(blocker)

    verified_nonlexical = _verified_nonlexical_windows(job_dir)
    min_gap_ms = int(os.environ.get("CHIRP_MID_GAP_MIN_MS", "5000"))
    unverified_gap_block_ms = int(
        os.environ.get("CHIRP_COMPLETENESS_UNVERIFIED_GAP_BLOCK_MS", "30000")
    )
    for before, after in zip(segments, segments[1:]):
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        start_ms = int(before.get("end_ms", 0))
        end_ms = int(after.get("start_ms", 0))
        gap_ms = end_ms - start_ms
        if gap_ms < min_gap_ms:
            continue
        if _covered_by_verified_nonlexical(start_ms, end_ms, verified_nonlexical):
            warnings.append(
                {
                    "reason": "audible_gap_verified_nonlexical_by_targeted_patch",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "gap_ms": gap_ms,
                }
            )
            continue
        patch_zero_words = _targeted_patch_zero_word_evidence(
            job_dir,
            start_ms,
            end_ms,
        )
        if patch_zero_words is not None:
            warnings.append(
                {
                    "reason": "audible_gap_verified_nonlexical_by_targeted_patch_words",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "gap_ms": gap_ms,
                    "recommended_action": "no_repeat_same_recognizer",
                    "patch_evidence": patch_zero_words,
                }
            )
            continue
        audible = probe(start_ms, end_ms)
        vad = vad_probe(start_ms, end_ms) if audible is True else None
        entry = {
            "reason": "audible_subtitle_gap" if audible is True else "unverified_subtitle_gap",
            "start_ms": max(0, start_ms - 10_000),
            "end_ms": min(audio_ms, end_ms + 10_000),
            "gap_start_ms": start_ms,
            "gap_end_ms": end_ms,
            "gap_ms": gap_ms,
            "audible": audible,
            "before_segment_id": before.get("segment_id"),
            "after_segment_id": after.get("segment_id"),
        }
        if vad is not None:
            entry["vad"] = vad
        if audible is True and isinstance(vad, dict) and vad.get("robust_non_speech") is True:
            warnings.append(
                {
                    **entry,
                    "reason": "audible_gap_vad_verified_non_speech",
                    "recommended_action": "no_paid_retry_required",
                }
            )
            continue
        if audible is True or (audible is None and gap_ms >= unverified_gap_block_ms):
            blockers.append(entry)
            repair_items.append(entry)
        else:
            warnings.append(entry)

    end_ms = int(segments[-1].get("end_ms", 0))
    uncovered = max(0, audio_ms - end_ms)
    tail_review_max_ms = int(os.environ.get("CHIRP_TAIL_REVIEW_MAX_MS", "3000"))
    tail = None
    if uncovered > 1000:
        tail = tail_coverage_assessment(
            end_ms, audio_ms, tail_review_max_ms, audibility_probe=probe
        )
        within = tail["within_tolerance_audible"]
        beyond = tail["beyond_tolerance_audible"]
        if uncovered > tail_review_max_ms and beyond is not False:
            patch_zero_words = _targeted_patch_zero_word_evidence(
                job_dir,
                end_ms,
                audio_ms,
            )
            if patch_zero_words is not None:
                warnings.append(
                    {
                        "reason": "audio_tail_verified_nonlexical_by_targeted_patch_words",
                        "start_ms": end_ms,
                        "end_ms": audio_ms,
                        "uncovered_ms": uncovered,
                        "recommended_action": "no_repeat_same_recognizer",
                        "patch_evidence": patch_zero_words,
                    }
                )
            else:
                entry = {
                    "reason": "uncovered_audio_tail",
                    "start_ms": end_ms,
                    "end_ms": audio_ms,
                    "uncovered_ms": uncovered,
                    "within_tolerance_audible": within,
                    "beyond_tolerance_audible": beyond,
                }
                blockers.append(entry)
                repair_items.append(entry)
        elif beyond is False and within is True:
            blockers.append(
                {
                    "reason": "short_audible_tail_requires_review",
                    "start_ms": end_ms,
                    "end_ms": min(audio_ms, end_ms + tail_review_max_ms),
                    "uncovered_ms": uncovered,
                }
            )
        else:
            warnings.append(
                {
                    "reason": "nonblocking_audio_tail",
                    "uncovered_ms": uncovered,
                    "within_tolerance_audible": within,
                    "beyond_tolerance_audible": beyond,
                }
            )

    status = "BLOCKED" if blockers else "PASS"
    report = {
        "schema_version": "chirp-completeness-v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "job_id": job_dir.name,
        "status": status,
        "handoff_allowed": status == "PASS",
        "paid_provider_calls": 0,
        "policy": {
            "gate_before_chatgpt_handoff": True,
            "auto_paid_retry": "bounded_multi_round_completeness_repair_with_budget_gate",
            "other_repairs": "auto_recheck_until_converged_or_safety_cap",
            "chirp_word_timestamps_authoritative": True,
            "local_vad_fail_closed": True,
            "vad_verified_nonspeech_is_nonblocking": True,
        },
        "summary": {
            "word_count": len(words),
            "segment_count": len(segments),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "repair_item_count": len(repair_items),
        },
        "audio": {"duration_ms": audio_ms, "subtitle_end_ms": end_ms, "tail": tail},
        "blockers": blockers,
        "warnings": warnings,
        "chunk_density": chunk_reports,
        "density_windows": density,
    }
    _atomic(job_dir / REPORT, report)
    _atomic(
        job_dir / REPAIR_PLAN,
        {
            "schema_version": "chirp-completeness-repair-v1",
            "generated_at": report["generated_at"],
            "status": "needs_review" if repair_items else "none",
            "policy": "no_new_paid_retry_without_existing_budget_or_explicit_review",
            "items": repair_items,
        },
    )
    return report


def main() -> int:
    report = evaluate(JOB)
    print(
        f"CHIRP_COMPLETENESS={report['status']} "
        f"blockers={report['summary']['blocker_count']} "
        f"warnings={report['summary']['warning_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
