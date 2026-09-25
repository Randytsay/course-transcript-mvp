"""Pre-handoff completeness gate for Chirp 3 evidence.

This gate runs before any ChatGPT handoff. It is provider-free: it only reads
existing Chirp artifacts and local audio, then blocks handoff when ASR
completeness is materially uncertain.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
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


def _verified_nonlexical_windows(job_dir: Path) -> list[dict[str, int]]:
    plan = _read_json(job_dir / "chirp-targeted-patch-plan.json", {})
    complete = _read_json(job_dir / "chirp-targeted-patch-complete.json", {})
    items = {
        int(item["patch_index"]): item
        for item in plan.get("items", [])
        if isinstance(item, dict) and item.get("patch_index") is not None
    } if isinstance(plan, dict) else {}
    windows: list[dict[str, int]] = []
    if not isinstance(complete, dict):
        return windows
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
        windows.append(
            {
                "start_ms": int(item.get("gap_start_ms", item.get("source_start_ms", 0))),
                "end_ms": int(item.get("gap_end_ms", item.get("source_end_ms", 0))),
            }
        )
    return windows


def _covered_by_verified_nonlexical(start_ms: int, end_ms: int, windows: list[dict[str, int]]) -> bool:
    duration = max(1, end_ms - start_ms)
    for window in windows:
        overlap = max(0, min(end_ms, window["end_ms"]) - max(start_ms, window["start_ms"]))
        if overlap / duration >= 0.80:
            return True
    return False


def evaluate(job_dir: Path = JOB, *, audibility_probe=None) -> dict[str, Any]:
    raw = _read_json(job_dir / "subtitles.json", {})
    merged = _read_json(job_dir / "merged-words.json", {})
    segments = raw.get("segments", []) if isinstance(raw, dict) else []
    words = merged.get("words", []) if isinstance(merged, dict) else []
    if not isinstance(segments, list) or not isinstance(words, list) or not segments:
        raise RuntimeError("Chirp completeness gate requires subtitles.json and merged-words.json")

    audio_ms = _audio_ms(job_dir)
    probe = audibility_probe or (lambda start, end: _audible(job_dir, start, end))
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
        audible = probe(start_ms, end_ms)
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
        "schema_version": "chirp-completeness-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "job_id": job_dir.name,
        "status": status,
        "handoff_allowed": status == "PASS",
        "paid_provider_calls": 0,
        "policy": {
            "gate_before_chatgpt_handoff": True,
            "auto_paid_retry": "existing_high_confidence_targeted_patch_only",
            "other_repairs": "block_and_review_before_handoff",
            "chirp_word_timestamps_authoritative": True,
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
