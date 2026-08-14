"""Cost-aware text correction cascade over immutable Chirp 3 segments.

Chirp 3 raw text and timestamps remain authoritative and are never modified.
A low-cost model handles normal correction windows. Only risky or invalid
segments are escalated to the stronger model. Any unresolved model failure
falls back to the original Chirp text.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from google.genai import types

from app.providers import correct_text as base

PRIMARY_MODEL = os.getenv("CORRECTION_PRIMARY_MODEL", "gemini-3.1-flash-lite")
ESCALATION_MODEL = os.getenv("CORRECTION_ESCALATION_MODEL", "gemini-3.7-flash")
PROMPT_VERSION = "fixed-segments-v6-cascade-gemini-3.7"
AUDIT_DIR = base.JOB / "correction-cascade-v1"
MAX_ATTEMPTS = max(1, int(os.getenv("CORRECTION_MODEL_MAX_ATTEMPTS", "3")))
ESCALATE_UNCERTAIN = os.getenv("CORRECTION_ESCALATE_UNCERTAIN", "true").lower() in {
    "1", "true", "yes", "on"
}


def content_guard(raw: str, corrected: str) -> list[str]:
    """Detect a likely rewrite while allowing normal spelling corrections."""
    raw_text = "".join(raw.split())
    corrected_text = "".join(corrected.split())
    reasons: list[str] = []
    if raw_text and not corrected_text:
        return ["empty_correction"]
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
        elif similarity < 0.15 and abs(len(corrected_text) - len(raw_text)) > max(
            12, round(len(raw_text) * 0.8)
        ):
            reasons.append("semantic_rewrite_risk")
    return reasons


def _source_digest(items: list[dict[str, Any]]) -> str:
    source = [
        {"segment_id": str(item["segment_id"]), "raw_text": str(item["raw_text"])}
        for item in items
    ]
    return hashlib.sha256(
        json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prompt(items: list[dict[str, Any]], terms: list[dict[str, Any]]) -> str:
    return (
        "Correct Traditional-Chinese ASR text only. Chirp 3 is the immutable "
        "source of segment IDs, order, and timestamps. Preserve meaning; do not "
        "summarize, add information, split, merge, reorder, or alter IDs. Apply "
        "only clear corrections. Return exactly one object for every input "
        "segment. Put unresolved words in uncertain_terms. JSON only.\n\n"
        "Global terminology:\n"
        + json.dumps(terms, ensure_ascii=False)
        + "\n\nSegments:\n"
        + json.dumps(
            [
                {"segment_id": str(item["segment_id"]), "text": item["raw_text"]}
                for item in items
            ],
            ensure_ascii=False,
        )
    )


def _generate(model: str, prompt: str) -> tuple[object, dict[str, Any]]:
    started = base.iso()
    retries: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_started = base.iso()
        try:
            response = base.client().models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=base.CORRECTION_SCHEMA,
                ),
            )
            completed = base.iso()
            return response, {
                "request_started_at": started,
                "response_completed_at": completed,
                "latency_ms": base.elapsed_ms(started, completed),
                "attempt_count": attempt,
                "retry_events": retries,
            }
        except Exception as exc:
            last_error = exc
            base._CLIENTS.client = None
            failed = base.iso()
            retries.append(
                {
                    "attempt": attempt,
                    "started_at": attempt_started,
                    "failed_at": failed,
                    "error_type": type(exc).__name__,
                }
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(10, 2**attempt))
    assert last_error is not None
    raise last_error


def _audit(
    *,
    model: str,
    items: list[dict[str, Any]],
    response: object | None,
    metrics: dict[str, Any] | None,
    valid: bool,
    segments: list[dict[str, Any]],
    error: Exception | None = None,
) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    digest = _source_digest(items)
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "-", model)
    path = AUDIT_DIR / f"{digest[:16]}.{safe_model}.{uuid.uuid4().hex[:12]}.json"
    raw_response = str(getattr(response, "text", "") or "") if response else ""
    record = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "source_sha256": digest,
        "source_segments": [
            {"segment_id": str(item["segment_id"]), "raw_text": item["raw_text"]}
            for item in items
        ],
        "response_valid": valid,
        "raw_response": raw_response,
        "usage_metadata": base._usage(response) if response else None,
        "segments": segments,
        "error_type": type(error).__name__ if error else None,
        **(metrics or {}),
    }
    base.atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def _run_model(
    model: str,
    items: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    response: object | None = None
    metrics: dict[str, Any] | None = None
    try:
        response, metrics = _generate(model, _prompt(items, terms))
        raw_response = str(getattr(response, "text", "") or "")
        payload = json.loads(raw_response)
        received = payload.get("segments", []) if isinstance(payload, dict) else []
    except Exception as exc:
        _audit(
            model=model,
            items=items,
            response=response,
            metrics=metrics,
            valid=False,
            segments=[],
            error=exc,
        )
        raise

    by_id = {
        str(entry.get("segment_id")): entry
        for entry in received
        if isinstance(entry, dict) and entry.get("segment_id") is not None
    }
    expected = [str(item["segment_id"]) for item in items]
    valid = len(received) == len(items) and set(by_id) == set(expected)
    results: list[dict[str, Any]] = []
    if valid:
        for item in items:
            segment_id = str(item["segment_id"])
            entry = by_id[segment_id]
            candidate = entry.get("corrected_text")
            if not isinstance(candidate, str):
                candidate = str(item["raw_text"])
            uncertain = entry.get("uncertain_terms", [])
            if not isinstance(uncertain, list):
                uncertain = []
            reasons = content_guard(str(item["raw_text"]), candidate)
            results.append(
                {
                    "segment_id": segment_id,
                    "corrected_text": candidate,
                    "uncertain_terms": [str(value) for value in uncertain],
                    "content_qa_reasons": reasons,
                    "model": model,
                }
            )
    _audit(
        model=model,
        items=items,
        response=response,
        metrics=metrics,
        valid=valid,
        segments=results,
    )
    if not valid:
        raise ValueError("model_response_missing_or_mismatched_segment_ids")
    return {str(item["segment_id"]): item for item in results}


def _needs_escalation(result: dict[str, Any]) -> list[str]:
    reasons = list(result.get("content_qa_reasons") or [])
    if ESCALATE_UNCERTAIN and result.get("uncertain_terms"):
        reasons.append("uncertain_terms")
    return sorted(set(str(reason) for reason in reasons))


def correct_window(
    items: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    raw_by_id = {str(item["segment_id"]): str(item["raw_text"]) for item in items}
    item_by_id = {str(item["segment_id"]): item for item in items}
    decisions: dict[str, dict[str, Any]] = {}

    try:
        primary = _run_model(PRIMARY_MODEL, items, terms)
        escalation_ids = [
            segment_id
            for segment_id, result in primary.items()
            if _needs_escalation(result)
        ]
    except Exception as exc:
        primary = {}
        escalation_ids = list(raw_by_id)
        primary_error = type(exc).__name__
    else:
        primary_error = None

    escalation: dict[str, dict[str, Any]] = {}
    escalation_error: str | None = None
    if escalation_ids:
        try:
            escalation = _run_model(
                ESCALATION_MODEL,
                [item_by_id[segment_id] for segment_id in escalation_ids],
                terms,
            )
        except Exception as exc:
            escalation_error = type(exc).__name__

    final: dict[str, dict[str, Any]] = {}
    for segment_id, raw_text in raw_by_id.items():
        primary_result = primary.get(segment_id)
        primary_reasons = (
            _needs_escalation(primary_result) if primary_result is not None else ["primary_error"]
        )
        if primary_result is not None and not primary_reasons:
            selected = primary_result
            route = "primary"
            fallback = False
            fallback_reason = None
        else:
            strong = escalation.get(segment_id)
            strong_reasons = _needs_escalation(strong) if strong is not None else ["escalation_error"]
            if strong is not None and not strong_reasons:
                selected = strong
                route = "escalated"
                fallback = False
                fallback_reason = None
            else:
                selected = {
                    "corrected_text": raw_text,
                    "uncertain_terms": list(
                        (strong or primary_result or {}).get("uncertain_terms") or []
                    ),
                    "content_qa_reasons": strong_reasons,
                    "model": "chirp-3-raw",
                }
                route = "chirp_raw_fallback"
                fallback = True
                fallback_reason = "cascade_failed:" + ",".join(strong_reasons)
        final[segment_id] = {
            "segment_id": segment_id,
            "corrected_text": selected["corrected_text"],
            "uncertain_terms": selected.get("uncertain_terms", []),
            "fallback_to_raw": fallback,
            "fallback_reason": fallback_reason,
            "content_qa_reasons": selected.get("content_qa_reasons", []),
            "selected_model": selected.get("model"),
            "correction_route": route,
        }
        decisions[segment_id] = {
            "segment_id": segment_id,
            "route": route,
            "selected_model": selected.get("model"),
            "primary_reasons": primary_reasons,
            "primary_error": primary_error,
            "escalation_error": escalation_error,
            "fallback_to_chirp_raw": fallback,
        }

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    route_path = AUDIT_DIR / f"routing.{_source_digest(items)[:16]}.json"
    base.atomic_text(
        route_path,
        json.dumps(
            {
                "prompt_version": PROMPT_VERSION,
                "primary_model": PRIMARY_MODEL,
                "escalation_model": ESCALATION_MODEL,
                "chirp_raw_immutable": True,
                "timestamps_immutable": True,
                "decisions": list(decisions.values()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return final


def _enrich_output() -> None:
    path = base.JOB / "subtitles-corrected.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    routes: dict[str, dict[str, Any]] = {}
    for route_path in sorted(AUDIT_DIR.glob("routing.*.json")):
        try:
            record = json.loads(route_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for decision in record.get("decisions", []):
            if isinstance(decision, dict) and decision.get("segment_id"):
                routes[str(decision["segment_id"])] = decision
    counts = {"primary": 0, "escalated": 0, "chirp_raw_fallback": 0}
    for segment in payload.get("segments", []):
        decision = routes.get(str(segment.get("segment_id")), {})
        route = str(decision.get("route") or "chirp_raw_fallback")
        segment["correction_route"] = route
        segment["selected_model"] = decision.get("selected_model", "chirp-3-raw")
        segment["chirp_raw_immutable"] = True
        counts[route] = counts.get(route, 0) + 1
    payload.update(
        {
            "source": "immutable Chirp 3 text/timestamps + cascaded text correction",
            "model": f"{PRIMARY_MODEL} -> {ESCALATION_MODEL} -> chirp-3-raw",
            "primary_model": PRIMARY_MODEL,
            "escalation_model": ESCALATION_MODEL,
            "prompt_version": PROMPT_VERSION,
            "chirp_raw_immutable": True,
            "timestamps_immutable": True,
            "correction_route_counts": counts,
        }
    )
    base.atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    # Glossary extraction uses the economical model. Window correction itself
    # does not rely on this mutable global and remains safe under concurrency.
    original_model = base.MODEL
    original_prompt = base.PROMPT_VERSION
    original_correct_window = base.correct_window
    try:
        base.MODEL = PRIMARY_MODEL
        base.PROMPT_VERSION = PROMPT_VERSION
        base.correct_window = correct_window
        result = base.main()
    finally:
        base.MODEL = original_model
        base.PROMPT_VERSION = original_prompt
        base.correct_window = original_correct_window
    if result == 0:
        _enrich_output()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
