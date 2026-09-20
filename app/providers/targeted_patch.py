"""Plan and run targeted Chirp patches for high-confidence timing collapses.

This module never replaces a base chunk.  It creates separate role=patch
chunks whose windows may overlay the merged timeline.  The merge layer owns
the final replacement decision.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.providers import run_chirp_pipeline as base
from app.providers.hardening_common import window_matches
from app.providers.qa_report import base_chunk_density_reports

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "")
JOB = DATA_DIR / "jobs" / JOB_NAME

PLAN = "chirp-targeted-patch-plan.json"
SUBMITTED = "chirp-targeted-patch-submitted.json"
WAITING = "chirp-targeted-patch-waiting.json"
COMPLETE = "chirp-targeted-patch-complete.json"


def _iso() -> str:
    return datetime.now(UTC).isoformat()


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


def build_plan(job_dir: Path = JOB, *, audibility_probe=None) -> dict[str, Any]:
    existing = _read_json(job_dir / PLAN, {})
    if isinstance(existing, dict) and existing.get("version") == "targeted-patch-v1":
        return existing

    audio_ms = _audio_ms(job_dir)
    probe = audibility_probe or (lambda start, end: _audible(job_dir, start, end))
    reports, _ = base_chunk_density_reports(job_dir, audio_ms, audibility_probe=probe)

    min_gap_ms = int(os.environ.get("CHIRP_AUTO_PATCH_MIN_GAP_SECONDS", "30")) * 1000
    max_window_ms = int(os.environ.get("CHIRP_AUTO_PATCH_MAX_SECONDS", "360")) * 1000
    margin_ms = int(os.environ.get("CHIRP_AUTO_PATCH_CONTEXT_SECONDS", "5")) * 1000
    max_items = max(1, int(os.environ.get("CHIRP_AUTO_PATCH_MAX_ITEMS", "1")))

    items: list[dict[str, Any]] = []
    for report in reports:
        if report.get("classification") != "explained_by_audible_zero_word_gap":
            continue
        if int(report.get("timing_repair_count") or 0) <= 0:
            continue
        parent_index = int(report["chunk_index"])
        parent_start = int(report["start_ms"])
        parent_end = int(report["end_ms"])
        for ordinal, gap in enumerate(report.get("zero_word_gaps") or []):
            if gap.get("audible") is not True:
                continue
            gap_start = int(gap["start_ms"])
            gap_end = int(gap["end_ms"])
            gap_ms = gap_end - gap_start
            if gap_ms < min_gap_ms:
                continue
            start_ms = max(parent_start, gap_start - margin_ms)
            end_ms = min(parent_end, gap_end + margin_ms)
            duration_ms = end_ms - start_ms
            if duration_ms <= 0 or duration_ms > max_window_ms:
                continue
            patch_index = 900_000 + parent_index * 100 + ordinal
            items.append(
                {
                    "patch_index": patch_index,
                    "parent_chunk_index": parent_index,
                    "source_start_ms": start_ms,
                    "source_end_ms": end_ms,
                    "duration_ms": duration_ms,
                    "gap_start_ms": gap_start,
                    "gap_end_ms": gap_end,
                    "gap_ms": gap_ms,
                    "role": "patch",
                    "patch_mode": "replace_window",
                    "reason": "provider_timing_collapse_with_audible_zero_word_gap",
                    "automatic_paid_retry": True,
                    "course_median_words_per_minute": report.get("course_median_words_per_minute"),
                    "raw_words_per_minute": report.get("words_per_minute"),
                    "adjusted_words_per_minute": report.get("adjusted_words_per_minute"),
                    "timing_repair_count": report.get("timing_repair_count"),
                }
            )
            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break

    payload = {
        "version": "targeted-patch-v1",
        "generated_at": _iso(),
        "job": job_dir.name,
        "status": "planned" if items else "none",
        "policy": "auto_only_high_confidence_timing_collapse_with_budget_gate",
        "items": items,
        "total_duration_ms": sum(int(item["duration_ms"]) for item in items),
    }
    _atomic(job_dir / PLAN, payload)
    return payload


def _plan_items(job_dir: Path) -> list[dict[str, Any]]:
    plan = build_plan(job_dir)
    items = plan.get("items") if isinstance(plan, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _env(item: dict[str, Any]) -> dict[str, str]:
    start_ms = int(item["source_start_ms"])
    end_ms = int(item["source_end_ms"])
    values = {
        "CHUNK_INDEX": str(int(item["patch_index"])),
        "CHUNK_START_SECONDS": f"{start_ms / 1000:.3f}",
        "CHUNK_END_SECONDS": f"{end_ms / 1000:.3f}",
        "CHUNK_ROLE": "patch",
        "CHUNK_PATCH_MODE": "replace_window",
        "CHIRP_DYNAMIC_BATCHING": "true",
    }
    return base.env_with(values)


def _manifest(job_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    return _read_json(
        job_dir / "chunks" / f"chunk-{int(item['patch_index']):03d}" / "manifest.json",
        {},
    )


def _submit(job_dir: Path, item: dict[str, Any]) -> tuple[bool, str]:
    prior = _manifest(job_dir, item)
    status = str(prior.get("status") or "")
    start = int(item["source_start_ms"]) / 1000
    end = int(item["source_end_ms"]) / 1000
    if status in {"SUBMITTED", "RUNNING", "RECOVERING", "SUCCEEDED", "EMPTY_SILENCE"}:
        if not window_matches(prior, start_seconds=start, end_seconds=end, dynamic_batching=True):
            return False, "retained targeted patch window is incompatible"
        return True, f"patch-{item['patch_index']}: retained {status}"
    env = _env(item)
    result = base.run_subprocess("app.providers.chirp_chunk_hardened", env, timeout=900)
    message = (result.stdout or "").strip()
    if result.returncode != 0:
        return False, f"{message}\n{base._diagnostic_excerpt(result.stderr or '')}".strip()
    return True, message


def _recover(job_dir: Path, item: dict[str, Any]) -> tuple[str, str]:
    manifest = _manifest(job_dir, item)
    status = str(manifest.get("status") or "")
    if status in {"SUCCEEDED", "EMPTY_SILENCE"}:
        return "done", f"patch-{item['patch_index']}: retained {status}"
    if status in {"", "FAILED", "CANCELLED"}:
        return "failed", f"patch-{item['patch_index']}: no recoverable operation ({status})"
    env = _env(item)
    env["ALLOW_PENDING"] = "1"
    result = base.run_subprocess("app.providers.recover_chunk_hardened", env, timeout=900)
    message = (result.stdout or "").strip()
    if result.returncode == 0:
        return "done", message
    if result.returncode == 75:
        return "pending", message
    if result.returncode == 76:
        return "retryable", message
    return "failed", f"{message}\n{base._diagnostic_excerpt(result.stderr or '')}".strip()


def _archive_derived(job_dir: Path) -> str:
    archive = job_dir / "targeted-patch-archives" / (
        time.strftime("patch-%Y%m%dT%H%M%SZ", time.gmtime())
    )
    archive.mkdir(parents=True, exist_ok=False)
    root_files = (
        "subtitles.json", "subtitles.srt", "subtitles.vtt",
        "subtitles-corrected.json", "review-terms.json", "terminology-consistency.json",
        "subtitles-cleaned.json", "subtitles-cleaned.srt", "transcript-cleaned.txt",
        "cleanup-review.json", "export-manifest.json", "qa-report.json", "qa-report.md",
        "qa_report.json", "qa_report.html", "density-retry-plan.json", "content-qa.json",
        "pipeline-manifest.json", "processing_manifest.json", "usage_report.json",
    )
    dirs = ("glossary", "correction-v2", "correction-cascade-v1", "correction-m3-v1")
    moved: list[str] = []
    for name in root_files:
        source = job_dir / name
        if source.exists():
            shutil.move(str(source), str(archive / name))
            moved.append(name)
    for name in dirs:
        source = job_dir / name
        if source.exists():
            shutil.move(str(source), str(archive / name))
            moved.append(name + "/")
    _atomic(
        archive / "manifest.json",
        {"created_at": _iso(), "reason": "targeted_patch_changed_merged_words", "moved": moved},
    )
    return str(archive.relative_to(job_dir))


def submit(job_dir: Path = JOB) -> int:
    items = _plan_items(job_dir)
    if not items:
        print("TARGETED_PATCH=NONE")
        return 0
    messages = []
    for item in items:
        ok, message = _submit(job_dir, item)
        messages.append(message)
        if not ok:
            print("\n".join(messages))
            print("TARGETED_PATCH=FAIL submission")
            return 1
    _atomic(
        job_dir / SUBMITTED,
        {
            "submitted_at": _iso(),
            "patch_count": len(items),
            "patch_indices": [int(item["patch_index"]) for item in items],
        },
    )
    _atomic(
        job_dir / WAITING,
        {"checked_at": _iso(), "pending": len(items), "patch_count": len(items)},
    )
    print("\n".join(messages))
    print(f"TARGETED_PATCH=SUBMITTED count={len(items)}")
    return 75


def recover(job_dir: Path = JOB) -> int:
    items = _plan_items(job_dir)
    if not items:
        print("TARGETED_PATCH=NONE")
        return 0
    counts = {"done": 0, "pending": 0, "retryable": 0, "failed": 0}
    messages = []
    for item in items:
        status, message = _recover(job_dir, item)
        counts[status] = counts.get(status, 0) + 1
        messages.append(message)
    print("\n".join(messages))
    if counts["failed"]:
        print("TARGETED_PATCH=FAIL recovery")
        return 1
    if counts["pending"] or counts["retryable"]:
        _atomic(job_dir / WAITING, {"checked_at": _iso(), **counts})
        print(
            f"TARGETED_PATCH=PENDING done={counts['done']} "
            f"pending={counts['pending']} retryable={counts['retryable']}"
        )
        return 76 if counts["retryable"] else 75

    result = base.run_subprocess("app.providers.merge_chunks", base.env_with({}), timeout=180)
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        print(base._diagnostic_excerpt(result.stderr or ""))
        print("TARGETED_PATCH=FAIL merge")
        return 1

    decisions = _read_json(job_dir / "merge-decisions.json", {})
    patch_indices = {int(item["patch_index"]) for item in items}
    patch_decisions = [
        item
        for item in decisions.get("patch_decisions", [])
        if isinstance(item, dict) and int(item.get("chunk_index", -1)) in patch_indices
    ]
    changed = any(
        bool(item.get("applied")) and int(item.get("patch_words_inserted") or 0) > 0
        for item in patch_decisions
    )
    archive = _archive_derived(job_dir) if changed else None
    verdicts = []
    for item in items:
        manifest = _manifest(job_dir, item)
        words_payload = _read_json(
            job_dir / "chunks" / f"chunk-{int(item['patch_index']):03d}" / "words.json",
            {},
        )
        patch_words = words_payload.get("words", []) if isinstance(words_payload, dict) else []
        if not isinstance(patch_words, list):
            patch_words = []
        gap_start = int(item["gap_start_ms"])
        gap_end = int(item["gap_end_ms"])
        target_gap_word_count = sum(
            1
            for word in patch_words
            if isinstance(word, dict)
            and gap_start
            <= (int(word.get("start_ms", 0)) + int(word.get("end_ms", 0))) // 2
            < gap_end
        )
        verdicts.append(
            {
                "patch_index": int(item["patch_index"]),
                "parent_chunk_index": int(item["parent_chunk_index"]),
                "status": manifest.get("status"),
                "word_count": int(manifest.get("word_count") or 0),
                "target_gap_word_count": target_gap_word_count,
                "patch_verdict": manifest.get("patch_verdict"),
                "operation_name": manifest.get("operation_name"),
            }
        )
    _atomic(
        job_dir / COMPLETE,
        {
            "completed_at": _iso(),
            "changed_merged_words": changed,
            "derived_archive": archive,
            "patch_decisions": patch_decisions,
            "verdicts": verdicts,
        },
    )
    (job_dir / WAITING).unlink(missing_ok=True)
    print(f"TARGETED_PATCH=PASS changed={str(changed).lower()}")
    return 0


def main() -> int:
    action = os.environ.get("CHIRP_PATCH_ACTION", "plan").strip().lower()
    if action == "plan":
        plan = build_plan(JOB)
        print(
            f"TARGETED_PATCH_PLAN={plan.get('status')} "
            f"items={len(plan.get('items') or [])}"
        )
        return 0
    if action == "submit":
        return submit(JOB)
    if action == "recover":
        return recover(JOB)
    raise SystemExit(f"unsupported CHIRP_PATCH_ACTION: {action}")


if __name__ == "__main__":
    raise SystemExit(main())
