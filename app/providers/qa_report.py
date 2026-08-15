"""Strict structural QA. A report is PASS only when publication is safe."""
from __future__ import annotations

import json
import os
import re
import subprocess
from html import escape
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")


def atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def duration_ms() -> int:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(JOB / "normalized.flac")], capture_output=True, text=True, check=True)
    return round(float(result.stdout.strip()) * 1000)


def audible_between(start_ms: int, end_ms: int) -> bool | None:
    if end_ms <= start_ms:
        return False
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            str(JOB / "normalized.flac"),
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
    if not match:
        return None
    return float(match.group(1)) > float(
        os.environ.get("CHIRP_SPEECH_MEAN_VOLUME_DB", "-50")
    )


def tail_patch_verified(end_ms: int, audio_ms: int) -> bool:
    """Return true when a targeted Chirp patch covered the residual tail.

    A volume threshold alone cannot distinguish trailing room noise or a
    recording's decay from speech.  When a successful patch covers the exact
    residual range and has no word after the final subtitle, Chirp has already
    examined that audio.  Keep it as a warning rather than falsely failing QA.
    """
    for manifest_path in (JOB / "chunks").glob("chunk-*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            manifest.get("role") != "patch"
            or manifest.get("status") not in {"SUCCEEDED", "EMPTY_SILENCE"}
            or int(manifest.get("source_start_ms", audio_ms + 1)) > end_ms
            or int(manifest.get("source_end_ms", 0)) < audio_ms
        ):
            continue
        words_path = manifest_path.with_name("words.json")
        try:
            words = json.loads(words_path.read_text(encoding="utf-8")).get("words", [])
        except (OSError, json.JSONDecodeError):
            continue
        if all(int(word.get("end_ms", 0)) <= end_ms for word in words):
            return True
    return False


def segment_quality(segments: list[dict[str, object]]) -> tuple[list[str], dict[str, object]]:
    """Detect provider timing/text collapses before publication.

    A short interjection is valid, but a cue containing hundreds of provider
    words is a hard structural failure.  A multi-second cue containing only
    one/two words is suspicious, but can also be a legitimate pause or a
    provider boundary artifact; keep it as an operator review item instead of
    failing the whole file.  Keep every offending ID in the report so a
    targeted chunk retry can be planned without rerunning a file.
    """
    max_words = int(os.environ.get("CHIRP_MAX_SEGMENT_WORDS", "120"))
    low_word_limit = int(os.environ.get("CHIRP_LOW_WORD_COUNT", "2"))
    low_word_duration_ms = int(
        os.environ.get("CHIRP_LOW_WORD_DURATION_MS", "10000")
    )
    errors: list[str] = []
    abnormal: list[dict[str, object]] = []
    review_items: list[dict[str, object]] = []
    for item in segments:
        try:
            word_count = int(item.get("word_count", -1))
            start_ms = int(item.get("start_ms", 0))
            end_ms = int(item.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        duration_ms_value = max(0, end_ms - start_ms)
        reasons: list[str] = []
        if word_count > max_words:
            reasons.append(f"word_count={word_count}>{max_words}")
        if duration_ms_value > low_word_duration_ms and 0 <= word_count <= low_word_limit:
            reasons.append(
                f"duration_ms={duration_ms_value}>{low_word_duration_ms}"
                f" with word_count={word_count}"
            )
        if reasons:
            entry = {
                "segment_id": item.get("segment_id"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "word_count": word_count,
                "duration_ms": duration_ms_value,
                "reasons": reasons,
                "hard_failure": word_count > max_words,
            }
            abnormal.append(entry)
            if (
                word_count <= max_words
                and duration_ms_value > low_word_duration_ms
                and 0 <= word_count <= low_word_limit
            ):
                review_items.append(
                    {
                        **entry,
                        "review_reason": "long_low_word_count_segment",
                    }
                )
    hard_abnormal = [item for item in abnormal if item["hard_failure"]]
    if hard_abnormal:
        preview = ", ".join(
            f"{item['segment_id']}({';'.join(item['reasons'])})"
            for item in hard_abnormal[:10]
        )
        errors.append(f"abnormal subtitle segment quality: {preview}")
    return errors, {
        "thresholds": {
            "max_segment_words": max_words,
            "low_word_count": low_word_limit,
            "low_word_duration_ms": low_word_duration_ms,
        },
        "abnormal_segments": abnormal,
        "hard_failure_segments": hard_abnormal,
        "review_segments": review_items,
    }


def density_windows(segments: list[dict[str, object]], audio_ms: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Measure transcript characters in fixed 15-minute windows.

    The final partial window is reported but not failed unless it reaches the
    minimum evaluation duration; this avoids treating a short tail as a bad
    recognition.  Each outlier becomes a durable, operator-reviewable patch
    plan rather than an automatic paid submission.
    """
    window_ms = int(os.environ.get("CHIRP_DENSITY_WINDOW_SECONDS", "900")) * 1000
    low = int(os.environ.get("CHIRP_DENSITY_LOW_CHARS", "2500"))
    high = int(os.environ.get("CHIRP_DENSITY_HIGH_CHARS", "3500"))
    minimum = int(os.environ.get("CHIRP_DENSITY_MIN_EVALUATED_SECONDS", "600")) * 1000
    windows: list[dict[str, object]] = []
    plans: list[dict[str, object]] = []
    for start in range(0, max(audio_ms, 1), window_ms):
        end = min(audio_ms, start + window_ms)
        chars = 0
        for item in segments:
            midpoint = (int(item.get("start_ms", 0)) + int(item.get("end_ms", 0))) // 2
            if start <= midpoint < end:
                chars += len(str(item.get("cleaned_text") or item.get("corrected_text") or item.get("raw_text") or item.get("text") or ""))
        evaluated = (end - start) >= minimum or end >= audio_ms and (end - start) >= minimum
        reason = None
        if evaluated and chars < low:
            reason = f"char_count={chars}<{low}"
        elif evaluated and chars > high:
            reason = f"char_count={chars}>{high}"
        item = {
            "window_index": len(windows) + 1,
            "start_ms": start,
            "end_ms": end,
            "duration_ms": end - start,
            "char_count": chars,
            "low_chars": low,
            "high_chars": high,
            "evaluated": evaluated,
            "reason": reason,
        }
        windows.append(item)
        if reason:
            plans.append({
                "plan_id": f"density-{len(plans)+1:03d}",
                "reason": "density_out_of_range",
                "detail": reason,
                "start_ms": max(0, start - 10_000),
                "end_ms": min(audio_ms, end + 10_000),
                "window_index": item["window_index"],
            })
    return windows, plans


def patch_word_density(
    manifest: dict[str, object],
    words: list[dict[str, object]],
    total_words: int,
    audio_ms: int,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Classify patch word counts by their covered audio duration.

    A raw ``patch_words`` count is not comparable across patches: a 20-second
    repair and a 3-minute repair can legitimately contain very different
    counts. Persist words/minute and compare it with deliberately broad bounds
    so impossible provider/timestamp collapses become review plans without
    rejecting a valid speech burst or silently publishing it.
    """
    start_ms = int(manifest.get("source_start_ms", 0) or 0)
    end_ms = int(manifest.get("source_end_ms", 0) or 0)
    duration_ms_value = max(0, end_ms - start_ms)
    word_count = len(words)
    words_per_minute = (
        word_count * 60_000 / duration_ms_value
        if duration_ms_value > 0
        else None
    )
    job_words_per_minute = (
        total_words * 60_000 / audio_ms
        if audio_ms > 0
        else None
    )
    low = float(os.environ.get("CHIRP_PATCH_MIN_WORDS_PER_MINUTE", "20"))
    high = float(os.environ.get("CHIRP_PATCH_MAX_WORDS_PER_MINUTE", "360"))
    reason: str | None = None
    if manifest.get("status") == "SUCCEEDED" and word_count == 0:
        reason = "successful_patch_has_no_words"
    elif word_count and duration_ms_value <= 0:
        reason = "patch_window_has_no_duration"
    elif words_per_minute is not None and not low <= words_per_minute <= high:
        reason = (
            f"words_per_minute={words_per_minute:.1f} outside "
            f"{low:.1f}..{high:.1f}"
        )
    report: dict[str, object] = {
        "chunk_index": manifest.get("chunk_index"),
        "status": manifest.get("status"),
        "source_start_ms": start_ms,
        "source_end_ms": end_ms,
        "duration_ms": duration_ms_value,
        "word_count": word_count,
        "words_per_minute": round(words_per_minute, 2) if words_per_minute is not None else None,
        "job_words_per_minute": round(job_words_per_minute, 2) if job_words_per_minute is not None else None,
        "rate_ratio_to_job": (
            round(words_per_minute / job_words_per_minute, 2)
            if words_per_minute is not None and job_words_per_minute
            else None
        ),
        "min_words_per_minute": low,
        "max_words_per_minute": high,
        "reason": reason,
        "review_required": reason is not None,
    }
    if reason is None:
        return report, None
    return report, {
        "reason": "patch_word_density_out_of_range",
        "detail": reason,
        "chunk_index": manifest.get("chunk_index"),
        "start_ms": max(0, start_ms - 10_000),
        "end_ms": min(audio_ms, end_ms + 10_000) if audio_ms > 0 else end_ms + 10_000,
    }


def patch_density_reports(
    job: Path,
    total_words: int,
    audio_ms: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    reports: list[dict[str, object]] = []
    plans: list[dict[str, object]] = []
    for manifest_path in sorted((job / "chunks").glob("chunk-*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("role") != "patch":
            continue
        try:
            payload = json.loads(
                manifest_path.with_name("words.json").read_text(encoding="utf-8")
            )
            words = payload.get("words", []) if isinstance(payload, dict) else []
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(words, list):
            words = []
        report, plan = patch_word_density(manifest, words, total_words, audio_ms)
        report["chunk"] = manifest_path.parent.name
        reports.append(report)
        if plan is not None:
            plan["plan_id"] = f"patch-density-{len(plans) + 1:03d}"
            plans.append(plan)
    return reports, plans


def audible_gap_plan(segments: list[dict[str, object]], audio_ms: int) -> tuple[list[str], list[dict[str, object]], list[dict[str, object]]]:
    errors: list[str] = []
    warnings: list[dict[str, object]] = []
    plans: list[dict[str, object]] = []
    minimum = int(os.environ.get("CHIRP_MID_GAP_MIN_MS", "5000"))
    for before, after in zip(segments, segments[1:]):
        gap = int(after.get("start_ms", 0)) - int(before.get("end_ms", 0))
        if gap < minimum:
            continue
        audible = audible_between(int(before.get("end_ms", 0)), int(after.get("start_ms", 0)))
        entry = {"before": before.get("segment_id"), "after": after.get("segment_id"), "gap_ms": gap, "audible": audible}
        if audible is True:
            errors.append(f"audible subtitle gap: {entry['before']}->{entry['after']} ({gap}ms)")
            plans.append({
                "plan_id": f"audible-gap-{len(plans)+1:03d}",
                "reason": "audible_subtitle_gap",
                "start_ms": max(0, int(before.get("end_ms", 0)) - 10_000),
                "end_ms": min(audio_ms, int(after.get("start_ms", 0)) + 10_000),
                "before_segment_id": before.get("segment_id"),
                "after_segment_id": after.get("segment_id"),
                "gap_ms": gap,
            })
        elif audible is False:
            warnings.append(entry)
        else:
            warnings.append({**entry, "warning": "unable_to_measure_audio"})
    return errors, warnings, plans


def main() -> int:
    merged = json.loads((JOB / "merged-words.json").read_text(encoding="utf-8"))
    raw = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    corrected_path = JOB / "subtitles-corrected.json"
    corrected = json.loads(corrected_path.read_text(encoding="utf-8")) if corrected_path.exists() else None
    words, segments = merged["words"], raw["segments"]
    errors, warnings, review_required = [], [], []
    if any(int(word["end_ms"]) <= int(word["start_ms"]) for word in words): errors.append("merged words contain non-positive durations")
    if any(next_word["start_ms"] < word["start_ms"] for word, next_word in zip(words, words[1:])): errors.append("merged word starts regress")
    ids = [item.get("segment_id") for item in segments]
    if len(ids) != len(set(ids)) or any(not value for value in ids): errors.append("segment IDs are not unique")
    invalid = [item["segment_id"] for item in segments if int(item["end_ms"]) <= int(item["start_ms"])]
    if invalid: errors.append(f"non-positive subtitle durations: {invalid[:10]}")
    timing_collisions = [
        item["segment_id"]
        for item in segments
        if item.get("timing_collision_merged")
    ]
    if timing_collisions:
        warnings.append(
            "subtitle timing collisions merged into prior cues: "
            f"{len(timing_collisions)}"
        )
    overlaps = [{"before": a["segment_id"], "after": b["segment_id"], "ms": int(a["end_ms"]) - int(b["start_ms"])} for a, b in zip(segments, segments[1:]) if int(b["start_ms"]) < int(a["end_ms"])]
    if overlaps: errors.append(f"subtitle overlaps: {len(overlaps)}")
    long_gaps = [{"before": a["segment_id"], "after": b["segment_id"], "gap_ms": int(b["start_ms"]) - int(a["end_ms"])} for a, b in zip(segments, segments[1:]) if int(b["start_ms"]) - int(a["end_ms"]) > 5000]
    if long_gaps: warnings.append(f"subtitle gaps over 5 seconds: {len(long_gaps)}")
    audio = duration_ms(); end = int(segments[-1]["end_ms"]) if segments else 0; uncovered = max(0, audio - end)
    gap_errors, audible_gaps, gap_plans = audible_gap_plan(segments, audio)
    # A volume reading can detect a suspicious gap but cannot prove spoken
    # words (music, room noise and applause are common).  Keep a durable patch
    # plan for human approval instead of failing a structurally valid job.
    review_required.extend(gap_errors)
    if audible_gaps:
        warnings.append(f"audible-gap measurements completed: {len(audible_gaps)} mid-file gaps")
    density, density_plans = density_windows(segments, audio)
    density_failures = [item for item in density if item.get("reason")]
    review_required.extend([f"15-minute character density out of range: window {item['window_index']} ({item['reason']})" for item in density_failures])
    patch_density, patch_density_plans = patch_density_reports(JOB, len(words), audio)
    review_required.extend([
        f"patch word density requires review: {item.get('chunk_index')} ({item.get('detail')})"
        for item in patch_density_plans
    ])
    retry_items = density_plans + gap_plans + patch_density_plans
    atomic(JOB / "density-retry-plan.json", {
        "version": "density-retry-plan-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "needs_review" if retry_items else "none",
        "policy": "plan_only_no_automatic_paid_retry",
        "items": retry_items,
    })
    segment_errors, segment_quality_report = segment_quality(segments)
    errors.extend(segment_errors)
    review_required.extend(
        [
            "long low-word-count subtitle segment requires review: "
            f"{item['segment_id']} ({';'.join(item['reasons'])})"
            for item in segment_quality_report.get("review_segments", [])
        ]
    )
    overrun_limit = int(os.environ.get("CHIRP_MAX_TIMELINE_OVERRUN_MS", "15000"))
    overrun_words = [
        word
        for word in words
        if int(word.get("end_ms", 0)) > audio + overrun_limit
    ]
    if overrun_words:
        errors.append(
            "merged words exceed audio duration: "
            f"{len(overrun_words)} words beyond {overrun_limit}ms tolerance"
        )
    if uncovered > 1000:
        tail_audible = audible_between(end, audio)
        tail_review_max_ms = int(
            os.environ.get("CHIRP_TAIL_REVIEW_MAX_MS", "3000")
        )
        if tail_audible is True and tail_patch_verified(end, audio):
            warnings.append(
                f"audible audio tail verified by targeted Chirp patch: {uncovered}ms"
            )
        elif uncovered <= tail_review_max_ms:
            review_required.append(
                f"uncovered audio tail requires review: {uncovered}ms "
                f"(audible={tail_audible})"
            )
        elif tail_audible is True:
            errors.append(f"audible audio tail uncovered: {uncovered}ms")
        elif tail_audible is False:
            warnings.append(f"silent audio tail not subtitled: {uncovered}ms")
        else:
            errors.append(f"unable to verify uncovered audio tail: {uncovered}ms")
    correction_invariant = None
    if corrected:
        correction_invariant = len(corrected["segments"]) == len(segments) and all((a["segment_id"], a["start_ms"], a["end_ms"]) == (b["segment_id"], b["start_ms"], b["end_ms"]) for a, b in zip(segments, corrected["segments"]))
        if not correction_invariant: warnings.append("existing correction is stale and requires a new segment-level correction pass")
    cleanup_report: dict[str, object] | None = None
    cleanup_path = JOB / "cleanup-review.json"
    if cleanup_path.is_file():
        try:
            loaded = json.loads(cleanup_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cleanup_report = {
                    "status": loaded.get("status"),
                    "summary": loaded.get("summary", {}),
                    "review_required": loaded.get("review_required", []),
                }
                review_count = int(
                    (cleanup_report.get("summary") or {}).get("review_count", 0)
                )
                if review_count:
                    warnings.append(f"automatic cleanup requires review: {review_count} segments")
        except (OSError, ValueError, TypeError):
            errors.append("cleanup-review.json is invalid")
    else:
        errors.append("missing cleanup-review.json")
    status = "FAIL" if errors else "REVIEW" if review_required else "PASS"
    report = {"generated_at": datetime.now(UTC).isoformat(), "job": JOB.name, "status": status, "errors": errors, "warnings": warnings, "review_required": review_required, "policy": {"long_low_word_count": "review_only", "tail_review_max_ms": int(os.environ.get("CHIRP_TAIL_REVIEW_MAX_MS", "3000")), "paid_retry": "plan_only_no_automatic_paid_retry"}, "audio": {"duration_ms": audio}, "chirp": {"model": "chirp_3", "word_count": len(words), "timeline_end_ms": merged.get("total_duration_ms"), "dropped_anomaly_count": merged.get("dropped_anomaly_count", 0), "timing_repair_count": merged.get("timing_repair_count", 0), "timeline_overrun_word_count": len(overrun_words)}, "subtitles": {"segment_count": len(segments), "end_ms": end, "uncovered_tail_ms": uncovered, "overlaps": overlaps, "long_gaps": long_gaps, "audible_gaps": audible_gaps, "segment_quality": segment_quality_report}, "density": {"windows": density, "failure_count": len(density_failures), "patch_windows": patch_density, "patch_failure_count": len(patch_density_plans), "retry_plan_count": len(retry_items)}, "correction": {"model": "gemini-3.7-flash" if corrected else None, "immutable_structure_preserved": correction_invariant}, "cleanup": cleanup_report}
    atomic(JOB / "qa-report.json", report)
    md = [f"# QA Report: {JOB.name}", "", f"Status: **{report['status']}**", "", "## Errors"] + ([f"- {item}" for item in errors] or ["- None"]) + ["", "## Review required"] + ([f"- {item}" for item in review_required] or ["- None"]) + ["", "## Warnings"] + ([f"- {item}" for item in warnings] or ["- None"])
    temporary = JOB / "qa-report.md.tmp"; temporary.write_text("\n".join(md) + "\n", encoding="utf-8"); temporary.replace(JOB / "qa-report.md")
    html = [
        "<!doctype html><html lang=\"zh-Hant\"><meta charset=\"utf-8\">",
        f"<title>QA Report: {escape(JOB.name)}</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;line-height:1.6}code{background:#f3f4f6;padding:2px 5px} .pass{color:#087f5b}.fail{color:#c92a2a}</style>",
        f"<h1>QA Report: {escape(JOB.name)}</h1>",
        f"<p>Status: <strong class=\"{'pass' if report['status'] == 'PASS' else 'fail'}\">{report['status']}</strong></p>",
        "<h2>Errors</h2><ul>",
        *([f"<li>{escape(item)}</li>" for item in errors] or ["<li>None</li>"]),
        "</ul><h2>Review required</h2><ul>",
        *([f"<li>{escape(item)}</li>" for item in review_required] or ["<li>None</li>"]),
        "</ul><h2>Warnings</h2><ul>",
        *([f"<li>{escape(item)}</li>" for item in warnings] or ["<li>None</li>"]),
        "</ul></html>",
    ]
    temporary = JOB / "qa_report.html.tmp"
    temporary.write_text("".join(html), encoding="utf-8")
    temporary.replace(JOB / "qa_report.html")
    atomic(JOB / "qa_report.json", report)
    print(f"QA={report['status']} errors={len(errors)} review={len(review_required)} warnings={len(warnings)}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
