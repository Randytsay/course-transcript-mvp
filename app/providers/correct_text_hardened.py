"""Gemini 3.6 correction with auditable splitting and content-safety guards."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from google.genai import types

from app.providers import correct_text as base

PROMPT_VERSION = "fixed-segments-v4-production-hardening"
SAFE_PROMPT_VERSION = re.sub(r"[^A-Za-z0-9._-]+", "-", PROMPT_VERSION).strip("-")


def generate_json(prompt: str, schema: dict[str, Any]) -> tuple[object, dict[str, Any]]:
    """Retry structured requests without deprecated Gemini sampling fields."""
    request_started_at = base.iso()
    last_error: Exception | None = None
    retries: list[dict[str, Any]] = []
    for attempt in range(1, 6):
        attempt_started_at = base.iso()
        try:
            response = base.client().models.generate_content(
                model=base.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            response_completed_at = base.iso()
            return response, {
                "request_started_at": request_started_at,
                "response_completed_at": response_completed_at,
                "latency_ms": base.elapsed_ms(
                    request_started_at,
                    response_completed_at,
                ),
                "attempt_count": attempt,
                "retry_events": retries,
            }
        except Exception as exc:
            last_error = exc
            base._CLIENTS.client = None
            failed_at = base.iso()
            retries.append(
                {
                    "attempt": attempt,
                    "started_at": attempt_started_at,
                    "failed_at": failed_at,
                    "latency_ms": base.elapsed_ms(attempt_started_at, failed_at),
                    "error_type": type(exc).__name__,
                }
            )
            if attempt == 5:
                raise
            time.sleep(min(30, 2**attempt))
    raise RuntimeError("unreachable") from last_error


def content_guard(raw: str, corrected: str) -> list[str]:
    """Return only severe rewrite indicators; normal spelling fixes remain allowed."""
    raw_text = "".join(raw.split())
    corrected_text = "".join(corrected.split())
    reasons: list[str] = []
    if raw_text and not corrected_text:
        reasons.append("empty_correction")
        return reasons
    if len(corrected_text) > 4_000:
        reasons.append("correction_too_long")
    if re.search(r"(.)\1{5,}", corrected_text):
        reasons.append("repeated_character_run")
    if len(raw_text) >= 8:
        ratio = len(corrected_text) / max(1, len(raw_text))
        if ratio < 0.30:
            reasons.append("excessive_deletion")
        elif ratio > 2.50:
            reasons.append("excessive_addition")
        similarity = SequenceMatcher(None, raw_text, corrected_text).ratio()
        if len(raw_text) >= 20 and similarity < 0.20:
            reasons.append("semantic_rewrite_risk")
        elif similarity < 0.15 and abs(
            len(corrected_text) - len(raw_text)
        ) > max(12, round(len(raw_text) * 0.8)):
            reasons.append("semantic_rewrite_risk")
    return reasons


def _source_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"segment_id": item["segment_id"], "raw_text": item["raw_text"]}
        for item in items
    ]


def _source_digest(source_segments: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            source_segments,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _record_prefix(items: list[dict[str, Any]], source_sha256: str) -> str:
    first = str(items[0]["segment_id"])
    return f"{first}.{SAFE_PROMPT_VERSION}.{source_sha256[:16]}"


def _new_record_path(
    items: list[dict[str, Any]],
    source_sha256: str,
    *,
    split: bool = False,
) -> Path:
    first = str(items[0]["segment_id"])
    token = uuid.uuid4().hex[:12]
    if split:
        last = str(items[-1]["segment_id"])
        return base.WORK / (
            f"{first}.split-{last}.{SAFE_PROMPT_VERSION}."
            f"{source_sha256[:16]}.{token}.json"
        )
    return base.WORK / f"{_record_prefix(items, source_sha256)}.{token}.json"


def _cached_window(
    items: list[dict[str, Any]],
    source_segments: list[dict[str, Any]],
    source_sha256: str,
) -> dict[str, dict[str, Any]] | None:
    expected = {str(item["segment_id"]) for item in items}
    pattern = f"{_record_prefix(items, source_sha256)}.*.json"
    for path in sorted(
        base.WORK.glob(pattern),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            record.get("model") != base.MODEL
            or record.get("source_segments") != source_segments
            or record.get("source_sha256") != source_sha256
            or record.get("prompt_version") != PROMPT_VERSION
            or not record.get("response_valid")
        ):
            continue
        cached = {
            str(entry["segment_id"]): entry
            for entry in record.get("segments", [])
            if isinstance(entry, dict) and entry.get("segment_id")
        }
        if set(cached) == expected:
            return cached
    return None


def correct_window(
    items: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    source_segments = _source_segments(items)
    source_sha256 = _source_digest(source_segments)
    cached = _cached_window(items, source_segments, source_sha256)
    if cached is not None:
        return cached

    prompt = (
        "Correct Traditional-Chinese ASR text only. Preserve meaning; do not "
        "summarize, add information, split, merge, reorder, or alter segment IDs/"
        "timestamps. Apply only clear corrections. Return exactly one object for "
        "every input segment with the same segment_id. uncertain_terms must list "
        "unresolved terms. JSON only.\n\nGlobal terminology:\n"
        + json.dumps(terms, ensure_ascii=False)
        + "\n\nSegments:\n"
        + json.dumps(
            [
                {"segment_id": item["segment_id"], "text": item["raw_text"]}
                for item in items
            ],
            ensure_ascii=False,
        )
    )
    response, metrics = generate_json(prompt, base.CORRECTION_SCHEMA)
    raw_response = str(getattr(response, "text", "") or "")
    try:
        received = json.loads(raw_response).get("segments", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        received = []
    by_id = {
        str(entry.get("segment_id")): entry
        for entry in received
        if isinstance(entry, dict) and entry.get("segment_id") is not None
    }
    expected_ids = [str(item["segment_id"]) for item in items]
    response_valid = set(by_id) == set(expected_ids) and len(received) == len(items)

    common_record = {
        "model": base.MODEL,
        "prompt_version": PROMPT_VERSION,
        "configured_window_ms": base.WINDOW_MS,
        "source_start_ms": items[0]["start_ms"],
        "source_end_ms": items[-1]["end_ms"],
        "source_segments": source_segments,
        "source_sha256": source_sha256,
        "usage_metadata": base._usage(response),
        "raw_response": raw_response,
        "response_segment_count": len(received),
        "expected_segment_count": len(items),
        "response_valid": response_valid,
        "cache_hit": False,
        **metrics,
    }

    if not response_valid and len(items) > 1:
        # Persist the paid parent response under a versioned, attempt-unique path
        # before splitting. No prior raw response or usage evidence is replaced.
        audit_path = _new_record_path(items, source_sha256, split=True)
        base.atomic_text(
            audit_path,
            json.dumps(
                {
                    **common_record,
                    "split_triggered": True,
                    "segments": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        midpoint = len(items) // 2
        return {
            **correct_window(items[:midpoint], terms),
            **correct_window(items[midpoint:], terms),
        }

    if not response_valid:
        final = list(
            base._fallback(
                items,
                "model_response_missing_or_mismatched_segment_ids",
            ).values()
        )
        for entry in final:
            entry["content_qa_reasons"] = ["invalid_structured_response"]
    else:
        final: list[dict[str, Any]] = []
        for item in items:
            segment_id = str(item["segment_id"])
            answer = by_id[segment_id]
            candidate = (
                answer.get("corrected_text")
                if isinstance(answer.get("corrected_text"), str)
                else item["raw_text"]
            )
            uncertain_terms = answer.get("uncertain_terms", [])
            if not isinstance(uncertain_terms, list):
                uncertain_terms = []
            reasons = content_guard(str(item["raw_text"]), str(candidate))
            final.append(
                {
                    "segment_id": segment_id,
                    "corrected_text": item["raw_text"] if reasons else candidate,
                    "uncertain_terms": [str(value) for value in uncertain_terms],
                    "fallback_to_raw": bool(reasons),
                    "fallback_reason": (
                        "content_guard:" + ",".join(reasons) if reasons else None
                    ),
                    "content_qa_reasons": reasons,
                }
            )

    # Every paid attempt receives its own immutable audit file. A later retry,
    # prompt revision, or changed window cannot erase earlier provider evidence.
    record_path = _new_record_path(items, source_sha256)
    base.atomic_text(
        record_path,
        json.dumps(
            {
                **common_record,
                "split_triggered": False,
                "segments": final,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return {str(entry["segment_id"]): entry for entry in final}


def _current_raw_by_id() -> dict[str, str]:
    try:
        payload = json.loads((base.JOB / "subtitles.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    return {
        str(item.get("segment_id")): str(item.get("raw_text", ""))
        for item in segments
        if isinstance(item, dict) and item.get("segment_id") is not None
    }


def _record_matches_current_source(
    record: dict[str, Any],
    raw_by_id: dict[str, str],
) -> bool:
    if record.get("prompt_version") != PROMPT_VERSION:
        return False
    source_segments = record.get("source_segments")
    if not isinstance(source_segments, list) or not source_segments:
        return False
    for item in source_segments:
        if not isinstance(item, dict):
            return False
        segment_id = str(item.get("segment_id"))
        if raw_by_id.get(segment_id) != str(item.get("raw_text", "")):
            return False
    return True


def _audit_details() -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    raw_by_id = _current_raw_by_id()
    paths = sorted(
        base.WORK.glob("*.json"),
        key=lambda item: item.stat().st_mtime,
    )
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or not _record_matches_current_source(
            record,
            raw_by_id,
        ):
            continue
        for entry in record.get("segments", []):
            if not isinstance(entry, dict) or not entry.get("segment_id"):
                continue
            details[str(entry["segment_id"])] = {
                "fallback_reason": entry.get("fallback_reason"),
                "content_qa_reasons": entry.get("content_qa_reasons", []),
            }
    return details


def _enrich_published_json() -> None:
    path = base.JOB / "subtitles-corrected.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    details = _audit_details()
    fallback_count = 0
    for item in payload.get("segments", []):
        if not isinstance(item, dict):
            continue
        detail = details.get(str(item.get("segment_id") or ""), {})
        item["fallback_reason"] = detail.get("fallback_reason")
        item["content_qa_reasons"] = detail.get("content_qa_reasons", [])
        if item.get("correction_fallback"):
            fallback_count += 1
    payload["prompt_version"] = PROMPT_VERSION
    payload["content_guard_fallback_count"] = fallback_count
    base.atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def main() -> int:
    base.PROMPT_VERSION = PROMPT_VERSION
    base.generate_json = generate_json
    base.correct_window = correct_window
    result = base.main()
    if result == 0:
        _enrich_published_json()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
