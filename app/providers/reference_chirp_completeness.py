"""Reference-driven Chirp 3 completeness gate."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.skills.dacheng_subtitle_review import (
    _human_semantic_units,
    sequential_word_timed_human_display_layer,
)

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "")
JOB = DATA_DIR / "jobs" / JOB_NAME
REPORT = "reference-chirp-completeness.json"
REFERENCE = "reference-transcript.txt"
PRE_PATCH = "merged-words.pre-reference-completeness.json"


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _normalize(value: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(value or ""))


def _word_text(words: list[dict[str, Any]]) -> str:
    return "".join(str(word.get("word") or "") for word in words)


def _ratio(reference: str, candidate: str) -> float:
    return SequenceMatcher(None, reference, candidate, autojunk=False).ratio() if reference and candidate else 0.0


def _coverage(reference: str, candidate: str) -> float:
    if not reference or not candidate:
        return 0.0
    matcher = SequenceMatcher(None, reference, candidate, autojunk=False)
    return sum(int(block.size) for block in matcher.get_matching_blocks()) / max(1, len(reference))


def _new_reference_ngrams(reference: str, original: str, rerun: str, *, size: int = 8) -> int:
    if len(reference) < size:
        return 0
    return len({
        reference[index:index + size]
        for index in range(len(reference) - size + 1)
        if reference[index:index + size] in rerun
        and reference[index:index + size] not in original
    })


def _paragraph_texts(reference_text: str) -> dict[int, str]:
    paragraphs: dict[int, list[str]] = {}
    for unit in _human_semantic_units(reference_text):
        paragraphs.setdefault(int(unit.get("paragraph_index") or 0), []).append(str(unit.get("text") or ""))
    return {key: "".join(value) for key, value in paragraphs.items()}


def _infer_failed_span(reports: list[dict[str, Any]], index: int) -> tuple[int, int] | None:
    previous_end = None
    next_start = None
    for prior in reversed(reports[:index]):
        value = prior.get("source_word_end_index")
        if isinstance(value, int):
            previous_end = value
            break
    for following in reports[index + 1:]:
        value = following.get("source_word_start_index")
        if isinstance(value, int):
            next_start = value
            break
    if previous_end is None or next_start is None or next_start <= previous_end + 1:
        return None
    return previous_end + 1, next_start - 1


def detect_candidates(
    reference_text: str,
    merged_words: dict[str, Any],
    *,
    max_candidates: int = 5,
    max_total_seconds: float = 1800.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    words = merged_words.get("words") if isinstance(merged_words, dict) else None
    if not isinstance(words, list) or not words:
        return [], {"reason": "missing_words"}
    _, alignment = sequential_word_timed_human_display_layer([], reference_text, {}, merged_words)
    reports = alignment.get("paragraph_reports") or []
    paragraph_text = _paragraph_texts(reference_text)
    raw: list[dict[str, Any]] = []
    for report_index, report in enumerate(reports):
        if not isinstance(report, dict):
            continue
        coverage = float(report.get("coverage") or 0.0)
        reason = str(report.get("reason") or "")
        paragraph_order = int(report.get("paragraph_order") or 0)
        word_start = report.get("source_word_start_index")
        word_end = report.get("source_word_end_index")
        eligible = bool(report.get("fail_closed_to_chirp_words")) and coverage >= 0.50
        if (
            not eligible
            and not bool(report.get("mapped"))
            and reason == "paragraph_unit_projection_failed"
            and coverage >= 0.75
        ):
            inferred = _infer_failed_span(reports, report_index)
            if inferred is not None:
                word_start, word_end = inferred
                eligible = True
        if not eligible or not isinstance(word_start, int) or not isinstance(word_end, int):
            continue
        if not (0 <= word_start <= word_end < len(words)):
            continue
        core_start = int(words[word_start].get("start_ms") or 0)
        core_end = int(words[word_end].get("end_ms") or 0)
        if core_end <= core_start:
            continue
        reference = paragraph_text.get(paragraph_order, "")
        normalized_reference = _normalize(reference)
        if len(normalized_reference) < 24:
            continue
        raw.append({
            "paragraph_order": paragraph_order,
            "reason": reason or "projection_failure",
            "coverage": round(coverage, 4),
            "word_start": word_start,
            "word_end": word_end,
            "core_start_ms": core_start,
            "core_end_ms": core_end,
            "duration_ms": core_end - core_start,
            "reference_text": reference,
            "reference_normalized": normalized_reference,
        })
    raw.sort(key=lambda item: int(item["core_start_ms"]))
    selected: list[dict[str, Any]] = []
    total_ms = 0
    for item in raw:
        if len(selected) >= max_candidates:
            break
        duration_ms = int(item["duration_ms"])
        if total_ms + duration_ms > int(max_total_seconds * 1000):
            continue
        if selected and int(item["core_start_ms"]) < int(selected[-1]["core_end_ms"]):
            continue
        selected.append(item)
        total_ms += duration_ms
    return selected, {
        "paragraph_count": alignment.get("paragraph_count"),
        "mapped_paragraph_count": alignment.get("mapped_paragraph_count"),
        "unmapped_unit_count": alignment.get("unmapped_unit_count"),
        "candidate_count_before_caps": len(raw),
        "candidate_count": len(selected),
        "candidate_duration_seconds": round(total_ms / 1000.0, 3),
    }


def _audio_duration_seconds() -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(JOB / "normalized.flac")],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def _provider_env(candidate: dict[str, Any], *, chunk_index: int) -> dict[str, str]:
    margin_ms = max(0, int(os.environ.get("REFERENCE_CHIRP_CONTEXT_SECONDS", "5")) * 1000)
    audio_duration_ms = int(_audio_duration_seconds() * 1000)
    start_ms = max(0, int(candidate["core_start_ms"]) - margin_ms)
    end_ms = min(audio_duration_ms, int(candidate["core_end_ms"]) + margin_ms)
    env = dict(os.environ)
    env.update({
        "JOB_NAME": JOB_NAME,
        "CHUNK_INDEX": str(chunk_index),
        "CHUNK_START_SECONDS": f"{start_ms / 1000:.3f}",
        "CHUNK_END_SECONDS": f"{end_ms / 1000:.3f}",
        "CHUNK_ROLE": "reference_audit",
        "CHUNK_PATCH_MODE": "audit_only",
        "CHIRP_DYNAMIC_BATCHING": "false",
        "ALLOW_PENDING": "1",
    })
    return env


def _run_module(module: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module], env=env, capture_output=True,
        text=True, check=False, timeout=900,
    )


def _recognize(candidate: dict[str, Any], *, chunk_index: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    env = _provider_env(candidate, chunk_index=chunk_index)
    submitted = _run_module("app.providers.chirp_chunk_hardened", env)
    if submitted.returncode != 0:
        return None, f"submit_failed:{submitted.returncode}"
    deadline = time.monotonic() + max(120, int(os.environ.get("REFERENCE_CHIRP_RECOVERY_TIMEOUT_SECONDS", "1200")))
    delay = max(5, int(os.environ.get("REFERENCE_CHIRP_POLL_SECONDS", "30")))
    while time.monotonic() < deadline:
        recovered = _run_module("app.providers.recover_chunk_hardened", env)
        if recovered.returncode == 0:
            path = JOB / "chunks" / f"chunk-{chunk_index:03d}" / "words.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None, "missing_words_after_success"
            words = payload.get("words") if isinstance(payload, dict) else None
            return (words if isinstance(words, list) else []), None
        if recovered.returncode not in {75, 76}:
            return None, f"recover_failed:{recovered.returncode}"
        time.sleep(delay)
        delay = min(120, delay * 2)
    return None, "recovery_timeout"


def _core_words(candidate: dict[str, Any], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = int(candidate["core_start_ms"])
    end = int(candidate["core_end_ms"])
    return [
        copy.deepcopy(word) for word in words
        if int(word.get("end_ms") or 0) > start and int(word.get("start_ms") or 0) < end
    ]


def _evaluate(candidate: dict[str, Any], original_words: list[dict[str, Any]], rerun_words: list[dict[str, Any]]) -> dict[str, Any]:
    reference = str(candidate["reference_normalized"])
    original = _normalize(_word_text(original_words))
    rerun = _normalize(_word_text(rerun_words))
    original_ratio = _ratio(reference, original)
    rerun_ratio = _ratio(reference, rerun)
    original_coverage = _coverage(reference, original)
    rerun_coverage = _coverage(reference, rerun)
    ngrams = _new_reference_ngrams(reference, original, rerun)
    ratio_delta = rerun_ratio - original_ratio
    coverage_delta = rerun_coverage - original_coverage
    improved = (
        ratio_delta >= float(os.environ.get("REFERENCE_CHIRP_MIN_RATIO_DELTA", "0.02"))
        or (
            coverage_delta >= float(os.environ.get("REFERENCE_CHIRP_MIN_COVERAGE_DELTA", "0.03"))
            and ngrams >= int(os.environ.get("REFERENCE_CHIRP_MIN_NEW_NGRAMS", "1"))
        )
    )
    return {
        "original_chars": len(original),
        "rerun_chars": len(rerun),
        "original_ratio": round(original_ratio, 4),
        "rerun_ratio": round(rerun_ratio, 4),
        "ratio_delta": round(ratio_delta, 4),
        "original_coverage": round(original_coverage, 4),
        "rerun_coverage": round(rerun_coverage, 4),
        "coverage_delta": round(coverage_delta, 4),
        "new_reference_8grams": ngrams,
        "improved": improved,
        "rerun_normalized": rerun,
    }


def _splice_words(
    original: list[dict[str, Any]],
    accepted: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = 0
    for candidate, replacement in sorted(accepted, key=lambda item: int(item[0]["word_start"])):
        start = int(candidate["word_start"])
        end = int(candidate["word_end"])
        if start < cursor:
            raise RuntimeError("overlapping reference completeness replacements")
        result.extend(copy.deepcopy(original[cursor:start]))
        result.extend(copy.deepcopy(replacement))
        cursor = end + 1
    result.extend(copy.deepcopy(original[cursor:]))
    return result


def main() -> int:
    report_path = JOB / REPORT
    reference_path = JOB / REFERENCE
    merged_path = JOB / "merged-words.json"
    if not reference_path.is_file() or reference_path.stat().st_size <= 0:
        _atomic_json(report_path, {"status": "SKIPPED", "reason": "reference_transcript_absent", "provider_calls": 0})
        print("REFERENCE_CHIRP_COMPLETENESS=SKIPPED no_reference")
        return 0
    if not merged_path.is_file() or not (JOB / "normalized.flac").is_file():
        _atomic_json(report_path, {"status": "SKIPPED", "reason": "required_chirp_evidence_absent", "provider_calls": 0})
        print("REFERENCE_CHIRP_COMPLETENESS=SKIPPED no_chirp_evidence")
        return 0
    if os.environ.get("COURSE_TRANSCRIPT_FAKE_PROVIDER", "").lower() in {"1", "true", "yes"}:
        _atomic_json(report_path, {"status": "SKIPPED", "reason": "fake_provider", "provider_calls": 0})
        print("REFERENCE_CHIRP_COMPLETENESS=SKIPPED fake_provider")
        return 0

    reference_text = reference_path.read_text(encoding="utf-8")
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    words = merged.get("words") if isinstance(merged, dict) else None
    if not isinstance(words, list) or not words:
        raise RuntimeError("merged-words.json has no words")
    candidates, alignment_summary = detect_candidates(
        reference_text, merged,
        max_candidates=max(1, int(os.environ.get("REFERENCE_CHIRP_MAX_CANDIDATES", "5"))),
        max_total_seconds=max(60.0, float(os.environ.get("REFERENCE_CHIRP_MAX_TOTAL_SECONDS", "1800"))),
    )
    if not candidates:
        _atomic_json(report_path, {
            "status": "PASS", "reason": "no_high_confidence_candidates",
            "provider_calls": 0, "alignment": alignment_summary, "accepted_count": 0,
        })
        print("REFERENCE_CHIRP_COMPLETENESS=PASS candidates=0")
        return 0

    context_seconds = max(0, int(os.environ.get("REFERENCE_CHIRP_CONTEXT_SECONDS", "5")))
    maximum_paid_seconds = sum(
        2 * (float(item["duration_ms"]) / 1000.0 + context_seconds * 2)
        for item in candidates
    )
    standard_rate = float(os.environ.get("REFERENCE_CHIRP_STANDARD_USD_PER_MINUTE", "0.016"))
    maximum_estimated_cost = maximum_paid_seconds / 60.0 * standard_rate
    automatic_cost_cap = max(0.0, float(os.environ.get("REFERENCE_CHIRP_AUTO_MAX_USD", "0.50")))
    if maximum_estimated_cost > automatic_cost_cap:
        _atomic_json(report_path, {
            "status": "REVIEW_REQUIRED",
            "reason": "automatic_cost_cap_exceeded",
            "provider_calls": 0,
            "alignment": alignment_summary,
            "candidate_count": len(candidates),
            "accepted_count": 0,
            "requires_review": True,
            "maximum_paid_audio_seconds": round(maximum_paid_seconds, 3),
            "maximum_estimated_cost_usd": round(maximum_estimated_cost, 4),
            "automatic_cost_cap_usd": round(automatic_cost_cap, 4),
        })
        print(
            "REFERENCE_CHIRP_COMPLETENESS=REVIEW_REQUIRED "
            f"cost_cap estimated={maximum_estimated_cost:.4f} cap={automatic_cost_cap:.4f}"
        )
        return 0

    accepted: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    results: list[dict[str, Any]] = []
    provider_calls = 0
    paid_audio_seconds = 0.0
    for candidate in candidates:
        paragraph = int(candidate["paragraph_order"])
        original_span = copy.deepcopy(words[int(candidate["word_start"]):int(candidate["word_end"]) + 1])
        run1_index = 970_000 + paragraph
        run1, error1 = _recognize(candidate, chunk_index=run1_index)
        provider_calls += 1
        paid_audio_seconds += (int(candidate["duration_ms"]) + 10_000) / 1000.0
        item = {k: v for k, v in candidate.items() if k not in {"reference_text", "reference_normalized"}}
        item["run1_chunk_index"] = run1_index
        if error1 or run1 is None:
            item.update({"accepted": False, "reason": error1 or "run1_failed"})
            results.append(item)
            continue
        core1 = _core_words(candidate, run1)
        evaluation = _evaluate(candidate, original_span, core1)
        item["run1"] = {k: v for k, v in evaluation.items() if k != "rerun_normalized"}
        if not evaluation["improved"]:
            item.update({"accepted": False, "reason": "no_material_improvement"})
            results.append(item)
            continue

        run2_index = 980_000 + paragraph
        run2, error2 = _recognize(candidate, chunk_index=run2_index)
        provider_calls += 1
        paid_audio_seconds += (int(candidate["duration_ms"]) + 10_000) / 1000.0
        item["run2_chunk_index"] = run2_index
        if error2 or run2 is None:
            item.update({"accepted": False, "reason": error2 or "run2_failed", "requires_review": True})
            results.append(item)
            continue
        core2 = _core_words(candidate, run2)
        identical = _normalize(_word_text(core2)) == str(evaluation["rerun_normalized"])
        item["run2_identical"] = identical
        if not identical:
            item.update({"accepted": False, "reason": "independent_rerun_mismatch", "requires_review": True})
            results.append(item)
            continue
        item.update({"accepted": True, "reason": "double_verified_improvement", "replacement_word_count": len(core1)})
        accepted.append((candidate, core1))
        results.append(item)

    if accepted:
        if not (JOB / PRE_PATCH).exists():
            shutil.copy2(merged_path, JOB / PRE_PATCH)
        patched = copy.deepcopy(merged)
        patched["words"] = _splice_words(words, accepted)
        patched["reference_completeness_patch"] = {
            "accepted_paragraphs": [int(candidate["paragraph_order"]) for candidate, _ in accepted],
            "policy": "double-independent-standard-batch-chirp3",
        }
        _atomic_json(merged_path, patched)

    requires_review = any(bool(item.get("requires_review")) for item in results)
    payload = {
        "status": "REVIEW_REQUIRED" if requires_review else "PASS",
        "policy": "reference-driven-chirp-completeness-v1",
        "processing_strategy": "STANDARD_BATCH",
        "alignment": alignment_summary,
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "provider_calls": provider_calls,
        "paid_audio_seconds": round(paid_audio_seconds, 3),
        "requires_review": requires_review,
        "merged_words_changed": bool(accepted),
        "results": results,
    }
    _atomic_json(report_path, payload)
    print(
        f"REFERENCE_CHIRP_COMPLETENESS={payload['status']} "
        f"candidates={len(candidates)} accepted={len(accepted)} provider_calls={provider_calls}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
