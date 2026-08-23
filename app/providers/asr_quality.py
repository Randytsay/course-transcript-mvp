"""Local, provider-free ASR quality analysis for completed Chirp chunks.

This module is Phase A of the PR #11 redesign.  It only reads existing job
artifacts and writes a derived ``asr-quality.json`` report.  It never submits a
paid recognition request and never mutates accepted transcript/chunk outputs.
"""
from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path
from typing import Any


_CHUNK_RE = re.compile(r"^chunk-(\d+)$")
_SUCCESS_STATUSES = {"SUCCEEDED", "EMPTY_SILENCE"}
_BASELINE_STATUSES = {"SUCCEEDED"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _char_count(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _repeat_ngram_ratio(words: list[dict[str, Any]], n: int = 3) -> float:
    tokens = [str(item.get("word", "")).strip() for item in words]
    tokens = [token for token in tokens if token]
    if len(tokens) < n * 2:
        return 0.0
    grams = [tuple(tokens[index:index + n]) for index in range(len(tokens) - n + 1)]
    if not grams:
        return 0.0
    repeats = len(grams) - len(set(grams))
    return round(repeats / len(grams), 4)


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _chunk_metrics(chunk_dir: Path) -> dict[str, Any]:
    match = _CHUNK_RE.match(chunk_dir.name)
    if not match:
        raise ValueError(f"invalid chunk directory: {chunk_dir}")
    chunk_index = int(match.group(1))
    manifest = _read_json(chunk_dir / "manifest.json", {})
    partial = _read_json(chunk_dir / "partial-transcript.json", {})
    words_payload = _read_json(chunk_dir / "words.json", {})
    words = words_payload.get("words", []) if isinstance(words_payload, dict) else []
    if not isinstance(words, list):
        words = []
    words = [item for item in words if isinstance(item, dict)]

    start_ms = int(
        manifest.get("source_start_ms")
        or partial.get("sourceStartMs")
        or 0
    )
    end_ms = int(
        manifest.get("source_end_ms")
        or partial.get("sourceEndMs")
        or start_ms
    )
    duration_ms = max(0, end_ms - start_ms)
    raw_text = str(partial.get("rawText") or "")
    char_count = _char_count(raw_text)
    word_count = int(
        partial.get("wordCount")
        or manifest.get("word_count")
        or manifest.get("words_count")
        or len(words)
        or 0
    )
    status = str(manifest.get("status") or partial.get("status") or "UNKNOWN")
    role = str(manifest.get("role") or "base")

    word_starts = [int(item.get("start_ms", 0)) for item in words if item.get("start_ms") is not None]
    word_ends = [int(item.get("end_ms", 0)) for item in words if item.get("end_ms") is not None]
    first_word_ms = (
        int(partial.get("firstWordMs"))
        if partial.get("firstWordMs") is not None
        else min(word_starts, default=None)
    )
    last_word_ms = (
        int(partial.get("lastWordMs"))
        if partial.get("lastWordMs") is not None
        else max(word_ends, default=None)
    )

    recognized_span_ratio = 0.0
    leading_gap_ratio = 0.0
    trailing_gap_ratio = 0.0
    longest_gap_ms = duration_ms if duration_ms > 0 and not words else 0
    if duration_ms > 0 and first_word_ms is not None and last_word_ms is not None:
        first = _clamp(first_word_ms, start_ms, end_ms)
        last = _clamp(last_word_ms, start_ms, end_ms)
        recognized_span_ratio = round(max(0, last - first) / duration_ms, 4)
        leading_gap_ratio = round(max(0, first - start_ms) / duration_ms, 4)
        trailing_gap_ratio = round(max(0, end_ms - last) / duration_ms, 4)
        gaps = [max(0, first - start_ms), max(0, end_ms - last)]
        ordered = sorted(
            (
                int(item.get("start_ms", 0)),
                int(item.get("end_ms", 0)),
            )
            for item in words
            if item.get("start_ms") is not None and item.get("end_ms") is not None
        )
        for previous, current in zip(ordered, ordered[1:]):
            gaps.append(max(0, current[0] - previous[1]))
        longest_gap_ms = max(gaps, default=0)

    density_chars_per_min = (
        round(char_count / (duration_ms / 60_000), 2)
        if duration_ms > 0 else 0.0
    )
    return {
        "chunk_index": chunk_index,
        "role": role,
        "status": status,
        "artifact_state": {
            "manifest": (chunk_dir / "manifest.json").is_file(),
            "partial_transcript": (chunk_dir / "partial-transcript.json").is_file(),
            "words": (chunk_dir / "words.json").is_file(),
        },
        "metrics": {
            "source_start_ms": start_ms,
            "source_end_ms": end_ms,
            "duration_ms": duration_ms,
            "char_count": char_count,
            "word_count": word_count,
            "density_chars_per_min": density_chars_per_min,
            "recognized_span_ratio": recognized_span_ratio,
            "leading_gap_ratio": leading_gap_ratio,
            "trailing_gap_ratio": trailing_gap_ratio,
            "longest_gap_ms": longest_gap_ms,
            "longest_gap_ratio": (
                round(longest_gap_ms / duration_ms, 4) if duration_ms > 0 else 0.0
            ),
            "repeat_trigram_ratio": _repeat_ngram_ratio(words),
        },
    }


def _severity(score: int) -> str:
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    if score == 1:
        return "low"
    return "normal"


def analyze_job(job_dir: Path) -> dict[str, Any]:
    """Return a deterministic course-relative quality report."""
    chunks_dir = Path(job_dir) / "chunks"
    chunk_dirs = sorted(
        (
            path for path in chunks_dir.iterdir()
            if path.is_dir() and _CHUNK_RE.match(path.name)
        ),
        key=lambda path: int(_CHUNK_RE.match(path.name).group(1)),  # type: ignore[union-attr]
    ) if chunks_dir.is_dir() else []

    chunks = [_chunk_metrics(path) for path in chunk_dirs]
    baseline_candidates = [
        chunk for chunk in chunks
        if chunk["status"] in _BASELINE_STATUSES
        and chunk["role"] == "base"
        and chunk["metrics"]["duration_ms"] >= 60_000
        and chunk["metrics"]["char_count"] > 0
    ]
    baseline_densities = [
        float(chunk["metrics"]["density_chars_per_min"])
        for chunk in baseline_candidates
        if float(chunk["metrics"]["density_chars_per_min"]) > 0
    ]
    median_density = (
        round(float(statistics.median(baseline_densities)), 2)
        if baseline_densities else 0.0
    )
    median_char_count = (
        round(float(statistics.median(
            int(chunk["metrics"]["char_count"]) for chunk in baseline_candidates
        )), 2)
        if baseline_candidates else 0.0
    )

    by_index = {int(chunk["chunk_index"]): chunk for chunk in chunks}
    ordered_indices = sorted(by_index)
    suspicious_count = 0
    for position, chunk_index in enumerate(ordered_indices):
        chunk = by_index[chunk_index]
        metrics = chunk["metrics"]
        reasons: list[str] = []
        score = 0

        relative_density = (
            round(float(metrics["density_chars_per_min"]) / median_density, 4)
            if median_density > 0 else None
        )
        metrics["course_median_density_chars_per_min"] = median_density
        metrics["course_median_char_count"] = median_char_count
        metrics["relative_density"] = relative_density

        neighbor_densities: list[float] = []
        for neighbor_position in (position - 1, position + 1):
            if 0 <= neighbor_position < len(ordered_indices):
                neighbor = by_index[ordered_indices[neighbor_position]]
                if neighbor["status"] == "SUCCEEDED" and neighbor["role"] == "base":
                    value = float(neighbor["metrics"]["density_chars_per_min"])
                    if value > 0:
                        neighbor_densities.append(value)
        neighbor_density = (
            float(statistics.median(neighbor_densities)) if neighbor_densities else 0.0
        )
        neighbor_ratio = (
            round(float(metrics["density_chars_per_min"]) / neighbor_density, 4)
            if neighbor_density > 0 else None
        )
        metrics["neighbor_median_density_chars_per_min"] = round(neighbor_density, 2)
        metrics["neighbor_density_ratio"] = neighbor_ratio

        artifact_state = chunk["artifact_state"]
        if chunk["status"] not in _SUCCESS_STATUSES:
            score += 4
            reasons.append("provider_or_chunk_status_not_successful")
        elif chunk["status"] == "EMPTY_SILENCE":
            # Chirp already verified this case against local audio volume. Zero
            # text is expected and must not become a paid-rerun suggestion.
            score = 0
            reasons = []
        else:
            if not artifact_state["partial_transcript"] or not artifact_state["words"]:
                score += 4
                reasons.append("expected_asr_artifact_missing")

            if relative_density is not None:
                if relative_density < 0.25:
                    score += 3
                    reasons.append("density_far_below_course_baseline")
                elif relative_density < 0.50:
                    score += 2
                    reasons.append("density_below_course_baseline")
                elif relative_density < 0.70:
                    score += 1
                    reasons.append("density_mildly_below_course_baseline")

            if neighbor_ratio is not None and neighbor_ratio < 0.35:
                score += 2
                reasons.append("density_far_below_neighbor_chunks")

            if (
                float(metrics["recognized_span_ratio"]) < 0.35
                and (relative_density is None or relative_density < 0.75)
                and int(metrics["duration_ms"]) >= 120_000
            ):
                score += 2
                reasons.append("recognized_timeline_span_low")

            if (
                float(metrics["longest_gap_ratio"]) > 0.55
                and (relative_density is None or relative_density < 0.75)
                and int(metrics["duration_ms"]) >= 120_000
            ):
                score += 1
                reasons.append("long_unrecognized_timeline_gap")

            if (
                float(metrics["repeat_trigram_ratio"]) > 0.45
                and int(metrics["word_count"]) >= 30
            ):
                score += 2
                reasons.append("high_repeated_word_pattern")

        severity = _severity(score)
        chunk["quality"] = {
            "score": score,
            "severity": severity,
            "suspicious": severity in {"medium", "high"},
            "reasons": reasons,
        }
        if chunk["quality"]["suspicious"]:
            suspicious_count += 1

    return {
        "schema_version": "asr-quality-v1",
        "job_id": Path(job_dir).name,
        "paid_provider_calls": 0,
        "baseline": {
            "eligible_chunk_count": len(baseline_candidates),
            "median_density_chars_per_min": median_density,
            "median_char_count": median_char_count,
        },
        "summary": {
            "chunk_count": len(chunks),
            "suspicious_chunk_count": suspicious_count,
            "high_count": sum(
                1 for chunk in chunks if chunk.get("quality", {}).get("severity") == "high"
            ),
            "medium_count": sum(
                1 for chunk in chunks if chunk.get("quality", {}).get("severity") == "medium"
            ),
        },
        "chunks": chunks,
    }


def write_report(job_dir: Path) -> dict[str, Any]:
    report = analyze_job(job_dir)
    _atomic_json(Path(job_dir) / "asr-quality.json", report)
    return report


def main() -> int:
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    job_name = os.environ.get("JOB_NAME", "").strip()
    if not job_name:
        raise SystemExit("JOB_NAME is required")
    report = write_report(data_dir / "jobs" / job_name)
    print(
        "ASR_QUALITY=PASS "
        f"chunks={report['summary']['chunk_count']} "
        f"suspicious={report['summary']['suspicious_chunk_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
