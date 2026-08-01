"""Gemini 3.6 Flash text-only correction for immutable subtitle segments."""
from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
WORK = JOB / "correction-v2"
MODEL = "gemini-3.6-flash"
WINDOW_MS = int(os.environ.get("GEMINI_CORRECTION_WINDOW_MS", "30000"))
MAX_WORKERS = max(1, int(os.environ.get("GEMINI_MAX_PARALLEL_WINDOWS", "2")))
PROMPT_VERSION = "fixed-segments-v2-observed"
_CLIENTS = threading.local()

TERMS_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "variants": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string"},
                },
                "required": ["canonical", "variants", "confidence"],
            },
        }
    },
    "required": ["terms"],
}
CORRECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_id": {"type": "string"},
                    "corrected_text": {"type": "string"},
                    "uncertain_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["segment_id", "corrected_text", "uncertain_terms"],
            },
        }
    },
    "required": ["segments"],
}


def iso() -> str:
    return datetime.now(UTC).isoformat()


def elapsed_ms(start: str, end: str) -> int:
    return max(
        0,
        round(
            (
                datetime.fromisoformat(end)
                - datetime.fromisoformat(start)
            ).total_seconds()
            * 1000
        ),
    )


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def client() -> genai.Client:
    existing = getattr(_CLIENTS, "client", None)
    if existing is None:
        existing = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
        )
        _CLIENTS.client = existing
    return existing


def generate_json(prompt: str, schema: dict[str, Any]) -> tuple[object, dict[str, Any]]:
    """Retry one text-only request and return auditable call timings."""
    request_started_at = iso()
    last_error: Exception | None = None
    retries: list[dict[str, Any]] = []
    for attempt in range(1, 6):
        attempt_started_at = iso()
        try:
            response = client().models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,
                ),
            )
            response_completed_at = iso()
            return response, {
                "request_started_at": request_started_at,
                "response_completed_at": response_completed_at,
                "latency_ms": elapsed_ms(
                    request_started_at,
                    response_completed_at,
                ),
                "attempt_count": attempt,
                "retry_events": retries,
            }
        except Exception as exc:
            last_error = exc
            _CLIENTS.client = None
            failed_at = iso()
            retries.append(
                {
                    "attempt": attempt,
                    "started_at": attempt_started_at,
                    "failed_at": failed_at,
                    "latency_ms": elapsed_ms(attempt_started_at, failed_at),
                    "error_type": type(exc).__name__,
                }
            )
            if attempt == 5:
                raise
            time.sleep(min(30, 2**attempt))
    raise RuntimeError("unreachable") from last_error


def _usage(response: object) -> dict[str, Any] | None:
    usage = getattr(response, "usage_metadata", None)
    return usage.model_dump(mode="json") if usage else None


def generate_terms(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = JOB / "glossary"
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "global-terms.json"
    source = [
        {"segment_id": item["segment_id"], "text": item["raw_text"]}
        for item in raw_segments
    ]
    source_sha256 = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("source_sha256") == source_sha256:
            return cached.get("terms", [])
    prompt = (
        "Extract only repeated or domain-specific terminology from this Traditional "
        "Chinese ASR transcript. Do not rewrite the transcript. For each term return "
        "a canonical spelling, observed variants, and confidence high/medium/low. "
        "Unknown terms must remain low confidence. JSON only.\n\n"
        + json.dumps(source, ensure_ascii=False)
    )
    response, metrics = generate_json(prompt, TERMS_SCHEMA)
    payload = json.loads(response.text)  # type: ignore[attr-defined]
    record = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "source_sha256": source_sha256,
        "usage_metadata": _usage(response),
        "terms": payload.get("terms", []),
        "raw_response": response.text,  # type: ignore[attr-defined]
        "cache_hit": False,
        **metrics,
    }
    atomic_text(cache, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    with (output / "global-terms.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["canonical", "variants", "confidence"],
        )
        writer.writeheader()
        for term in record["terms"]:
            writer.writerow(
                {
                    "canonical": term.get("canonical", ""),
                    "variants": " | ".join(term.get("variants", [])),
                    "confidence": term.get("confidence", ""),
                }
            )
    return record["terms"]


def windows(segments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    start: int | None = None
    for segment in segments:
        if start is None:
            start = int(segment["start_ms"])
        if current and int(segment["end_ms"]) - start >= WINDOW_MS:
            result.append(current)
            current = []
            start = int(segment["start_ms"])
        current.append(segment)
    if current:
        result.append(current)
    return result


def correct_window(
    items: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    first = str(items[0]["segment_id"])
    path = WORK / f"{first}.json"
    source_segments = [
        {"segment_id": item["segment_id"], "raw_text": item["raw_text"]}
        for item in items
    ]
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            record.get("model") == MODEL
            and record.get("source_segments") == source_segments
            and record.get("prompt_version") == PROMPT_VERSION
        ):
            return {
                entry["segment_id"]: entry
                for entry in record.get("segments", [])
            }
    prompt = (
        "Correct Traditional-Chinese ASR text only. Preserve meaning; do not "
        "summarize, add information, split, merge, reorder, or alter segment IDs/"
        "timestamps. Apply only clear corrections. Return exactly one object for "
        "every input segment with the same segment_id. uncertain_terms must list "
        "unresolved terms.\n\nGlobal terminology:\n"
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
    response, metrics = generate_json(prompt, CORRECTION_SCHEMA)
    received = json.loads(response.text).get("segments", [])  # type: ignore[attr-defined]
    by_id = {entry.get("segment_id"): entry for entry in received}
    final: list[dict[str, Any]] = []
    for item in items:
        answer = by_id.get(item["segment_id"], {})
        text = (
            answer.get("corrected_text")
            if isinstance(answer.get("corrected_text"), str)
            else item["raw_text"]
        )
        final.append(
            {
                "segment_id": item["segment_id"],
                "corrected_text": text,
                "uncertain_terms": answer.get("uncertain_terms", []),
                "fallback_to_raw": item["segment_id"] not in by_id,
            }
        )
    record = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "source_start_ms": items[0]["start_ms"],
        "source_end_ms": items[-1]["end_ms"],
        "source_segments": source_segments,
        "source_sha256": hashlib.sha256(
            json.dumps(
                source_segments,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "usage_metadata": _usage(response),
        "raw_response": response.text,  # type: ignore[attr-defined]
        "segments": final,
        "cache_hit": False,
        **metrics,
    }
    atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return {entry["segment_id"]: entry for entry in final}


def timestamp(value: int, separator: str = ",") -> str:
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def write_review_terms(
    final: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> None:
    variant_to_canonical: dict[str, str] = {}
    confidence_by_term: dict[str, str] = {}
    for term in terms:
        canonical = str(term.get("canonical", "")).strip()
        confidence = str(term.get("confidence", "low")).lower()
        if canonical:
            variant_to_canonical[canonical] = canonical
            confidence_by_term[canonical] = confidence
        for variant in term.get("variants", []):
            value = str(variant).strip()
            if value:
                variant_to_canonical[value] = canonical or value
                confidence_by_term[value] = confidence
    prior_path = JOB / "review-terms.json"
    prior = (
        {
            item["id"]: item
            for item in json.loads(prior_path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("id")
        }
        if prior_path.exists()
        else {}
    )
    review: list[dict[str, Any]] = []
    seen: set[str] = set()
    for segment in final:
        for unresolved in segment.get("uncertain_terms", []):
            heard = str(unresolved).strip()
            if not heard or heard in seen:
                continue
            seen.add(heard)
            term_id = hashlib.sha256(heard.encode("utf-8")).hexdigest()[:16]
            existing = prior.get(term_id, {})
            review.append(
                {
                    "id": term_id,
                    "heard": heard,
                    "suggestion": variant_to_canonical.get(heard, heard),
                    "timestamp": timestamp(int(segment["start_ms"]))[:-4],
                    "confidence": (
                        "medium"
                        if confidence_by_term.get(heard) == "medium"
                        else "low"
                    ),
                    "status": existing.get("status", "pending"),
                    "scope": existing.get("scope", "session"),
                    "approved_value": existing.get("approved_value"),
                    "decided_by": existing.get("decided_by"),
                    "decided_at": existing.get("decided_at"),
                }
            )
    atomic_text(
        prior_path,
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
    )


def main() -> int:
    source = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    raw = source["segments"]
    if not raw or any(item["end_ms"] <= item["start_ms"] for item in raw):
        print("CORRECT=FAIL invalid raw subtitle segments")
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    terms = generate_terms(raw)
    groups = windows(raw)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda group: correct_window(group, terms), groups))
    corrected = {key: value for result in results for key, value in result.items()}
    final: list[dict[str, Any]] = []
    for item in raw:
        answer = corrected[item["segment_id"]]
        final.append(
            {
                **item,
                "corrected_text": answer["corrected_text"],
                "text": answer["corrected_text"],
                "uncertain_terms": answer["uncertain_terms"],
                "corrected": answer["corrected_text"] != item["raw_text"],
            }
        )
    payload = {
        "source": "chirp_3_merged + gemini-3.6-flash segment correction",
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "window_count": len(groups),
        "parallelism": MAX_WORKERS,
        "segment_count": len(final),
        "corrected_count": sum(item["corrected"] for item in final),
        "total_duration_ms": final[-1]["end_ms"],
        "segments": final,
    }
    atomic_text(
        JOB / "subtitles-corrected.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_text(
        JOB / "subtitles-corrected.srt",
        "\n\n".join(
            f"{index}\n{timestamp(item['start_ms'])} --> "
            f"{timestamp(item['end_ms'])}\n{item['corrected_text']}"
            for index, item in enumerate(final, 1)
        )
        + "\n",
    )
    atomic_text(
        JOB / "subtitles-corrected.vtt",
        "WEBVTT\n\n"
        + "\n\n".join(
            f"{timestamp(item['start_ms'], '.')} --> "
            f"{timestamp(item['end_ms'], '.')}\n{item['corrected_text']}"
            for item in final
        )
        + "\n",
    )
    atomic_text(
        JOB / "transcript-corrected.txt",
        "\n".join(item["corrected_text"] for item in final) + "\n",
    )
    atomic_text(
        JOB / "transcript-corrected.md",
        "# 校正逐字稿\n\n"
        + "\n".join(
            f"[{timestamp(item['start_ms'])[:-4]}] {item['corrected_text']}"
            for item in final
        )
        + "\n",
    )
    write_review_terms(final, terms)
    print(
        f"CORRECT=PASS segments={len(final)} changed={payload['corrected_count']} "
        f"terms={len(terms)} windows={len(groups)} workers={MAX_WORKERS}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
