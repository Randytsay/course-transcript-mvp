#!/usr/bin/env python3
"""Bounded MiniMax-M3 thinking-disabled validation.

This is an operator-run diagnostic. It reads one immutable subtitle source and
the existing sanitized Phase-B comparison artifact, calls MiniMax directly,
and writes only hashes/metrics/schema metadata. Provider bodies and transcript
text never leave process memory and are not persisted.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError

from app.providers.minimax_provider import (
    MiniMaxCorrectionClient,
    MiniMaxProviderError,
    _default_http_post,
)


SOURCE = Path("/opt/course-transcript-source/data/jobs/260815-20260816-152635-39ffbc/subtitles.json")
OUTPUT = Path("/opt/course-transcript-source/data/m3-validation/phase-d-thinking-disabled-20260817/results.json")
BASELINE = Path("/opt/course-transcript-source/data/m3-validation/phase-b-20260817/ab-10min-full/results.json")
KEY_FILE = Path("/opt/course-transcript/secrets/minimax-api-key")
MODEL = "MiniMax-M3"
PHASE_C_WINDOWS = [(offset, min(10, 107 - offset)) for offset in range(0, 100, 10)] + [(100, 7)]


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def safe_error(exc: BaseException) -> dict[str, object]:
    result: dict[str, object] = {"type": type(exc).__name__}
    if isinstance(exc, MiniMaxProviderError):
        result["kind"] = exc.kind.value
        result["status_code"] = exc.status_code
        raw = exc.raw_response
        if isinstance(raw, Mapping):
            result["provider_error_keys"] = sorted(str(key) for key in raw.keys())
            base = raw.get("base_resp")
            if isinstance(base, Mapping):
                result["provider_status_code"] = base.get("status_code")
                for key in ("status_msg", "statusMessage", "message", "code"):
                    if base.get(key) not in (None, ""):
                        result["provider_error_field"] = key
                        break
    return result


def response_meta(status: int, raw_body: bytes, latency_ms: int) -> dict[str, object]:
    meta: dict[str, object] = {
        "status_code": status,
        "body_bytes": len(raw_body),
        "body_sha256": hashlib.sha256(raw_body).hexdigest(),
        "latency_ms": latency_ms,
    }
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        meta["json_valid"] = False
        return meta
    meta["json_valid"] = True
    if isinstance(payload, Mapping):
        meta["returned_model"] = payload.get("model")
        base = payload.get("base_resp")
        if isinstance(base, Mapping):
            meta["provider_status_code"] = base.get("status_code")
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            meta["finish_reason"] = choices[0].get("finish_reason")
            message = choices[0].get("message")
            meta["message_content_present"] = isinstance(message, Mapping) and isinstance(message.get("content"), (str, list))
        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            details = usage.get("completion_tokens_details")
            details = details if isinstance(details, Mapping) else {}
            meta["usage"] = {
                "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", usage.get("input_token_count", 0))),
                "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", usage.get("output_token_count", 0))),
                "reasoning_tokens": details.get("reasoning_tokens", usage.get("reasoning_tokens", 0)),
                "total_tokens": usage.get("total_tokens", 0),
            }
            meta["usage_available"] = True
        else:
            meta["usage_available"] = False
    return meta


def load_source() -> tuple[list[dict[str, object]], str]:
    raw = SOURCE.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)
    segments = payload["segments"]
    items = [
        {
            "segment_id": str(item["segment_id"]),
            "start_ms": int(item["start_ms"]),
            "end_ms": int(item["end_ms"]),
            "raw_text": str(item["raw_text"]),
        }
        for item in segments
    ]
    return items, source_hash


def load_baselines() -> dict[str, list[dict[str, object]]]:
    if not BASELINE.exists():
        return {}
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    records = payload.get("records", {})
    return {str(provider): list(rows) for provider, rows in records.items() if isinstance(rows, list)}


def compare_baselines(
    items: list[dict[str, object]],
    result: dict[str, dict[str, object]],
    baselines: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    ids = [str(item["segment_id"]) for item in items]
    comparison: dict[str, object] = {"available": False}
    for provider, rows in baselines.items():
        match = next((row for row in rows if row.get("source_segment_ids") == ids), None)
        if not isinstance(match, Mapping) or not isinstance(match.get("result"), Mapping):
            continue
        baseline_result = match["result"]
        same = 0
        compared = 0
        for sid in ids:
            current = result.get(sid, {}).get("corrected_text")
            prior = baseline_result.get(sid, {}).get("corrected_text") if isinstance(baseline_result.get(sid), Mapping) else None
            if isinstance(current, str) and isinstance(prior, str):
                compared += 1
                same += int(current == prior)
        comparison[provider] = {"same_text_segments": same, "compared_segments": compared}
        comparison["available"] = True
    return comparison


def run_window(
    *,
    client: MiniMaxCorrectionClient,
    items: list[dict[str, object]],
    baselines: dict[str, list[dict[str, object]]],
    label: str,
    offset: int,
    window_size: int,
) -> dict[str, object]:
    request_records: list[dict[str, object]] = []

    def capture_post(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> tuple[int, Mapping[str, str], bytes]:
        started = time.monotonic()
        try:
            status, response_headers, raw_body = _default_http_post(url, headers, body, timeout)
            request_records.append(response_meta(status, raw_body, round((time.monotonic() - started) * 1000)))
            return status, response_headers, raw_body
        except HTTPError as exc:
            try:
                raw_body = exc.read()
            except OSError:
                raw_body = b""
            request_records.append(response_meta(int(exc.code), raw_body, round((time.monotonic() - started) * 1000)))
            raise
        except BaseException as exc:
            request_records.append({
                "status_code": None,
                "exception_type": type(exc).__name__,
                "latency_ms": round((time.monotonic() - started) * 1000),
            })
            raise

    client.http_post = capture_post
    started = time.monotonic()
    expected_ids = [str(item["segment_id"]) for item in items]
    record: dict[str, object] = {
        "label": label,
        "offset": offset,
        "window_size": window_size,
        "source_segment_ids": expected_ids,
        "source_sha256": sha256_json([{"segment_id": item["segment_id"], "raw_text": item["raw_text"]} for item in items]),
        "input_characters": sum(len(str(item["raw_text"])) for item in items),
        "request_config": {
            "stream": False,
            "thinking": {"type": "disabled"},
            "reasoning_split": True,
            "max_completion_tokens": 4096,
        },
    }
    try:
        result = client.correct_window(items, [], context="")
        result_ids = list(result.keys())
        invariant = {
            "segment_ids_unchanged": result_ids == expected_ids,
            "segment_order_unchanged": result_ids == expected_ids,
            "timestamps_not_emitted": all(
                not any(key in value for key in ("start_ms", "end_ms", "start", "end"))
                for value in result.values()
                if isinstance(value, Mapping)
            ),
        }
        record.update({
            "valid": all(invariant.values()),
            "finish_reason": request_records[-1].get("finish_reason") if request_records else None,
            "returned_model": request_records[-1].get("returned_model") if request_records else None,
            "usage": request_records[-1].get("usage") if request_records else None,
            "usage_available": bool(request_records and request_records[-1].get("usage_available")),
            "invariant": invariant,
            "content_guard_fallback_segments": sum(int(bool(value.get("fallback_to_raw"))) for value in result.values()),
            "content_guard_reasons": sorted({reason for value in result.values() for reason in value.get("content_qa_reasons", [])}),
            "quality_comparison": compare_baselines(items, result, baselines),
            "result_sha256": sha256_json(result),
        })
    except BaseException as exc:
        record.update({
            "valid": False,
            "error": safe_error(exc),
            "finish_reason": request_records[-1].get("finish_reason") if request_records else None,
            "returned_model": request_records[-1].get("returned_model") if request_records else None,
            "usage": request_records[-1].get("usage") if request_records else None,
            "usage_available": bool(request_records and request_records[-1].get("usage_available")),
        })
    record["latency_ms"] = round((time.monotonic() - started) * 1000)
    record["request_count"] = len(request_records)
    record["request_metadata"] = request_records
    return record


def main() -> int:
    os.environ["MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS"] = "1"
    os.environ["MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS"] = "1"
    os.environ["MINIMAX_M3_TIMEOUT_SECONDS"] = "60"
    os.environ["MINIMAX_M3_MAX_OUTPUT_TOKENS"] = "4096"
    os.environ["MINIMAX_M3_CORRECTION_THINKING_MODE"] = "disabled"
    os.environ["MINIMAX_M3_TERMINOLOGY_THINKING_MODE"] = "adaptive"
    os.environ["MINIMAX_M3_REASONING_SPLIT"] = "true"

    items, source_hash = load_source()
    baselines = load_baselines()
    client = MiniMaxCorrectionClient(key_file=KEY_FILE, model=MODEL, audit_dir=None)

    canary_items = items[:3]
    canary = run_window(
        client=client,
        items=canary_items,
        baselines=baselines,
        label="direct-canary",
        offset=0,
        window_size=3,
    )
    if not canary.get("valid") or canary.get("finish_reason") != "stop":
        report = {
            "schema": "m3-thinking-disabled-validation-v1",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": MODEL,
            "source_job": "260815-20260816-152635-39ffbc",
            "source_file_sha256": source_hash,
            "source_segment_count": len(items),
            "thinking_mode": "disabled",
            "terminology_thinking_mode": "adaptive",
            "canary": canary,
            "replay_not_run": True,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"canary": canary, "replay_not_run": True}, ensure_ascii=False, indent=2))
        return 2

    records = [canary]
    for offset, window_size in PHASE_C_WINDOWS:
        window_items = items[offset : offset + window_size]
        records.append(
            run_window(
                client=client,
                items=window_items,
                baselines=baselines,
                label="phase-c-replay",
                offset=offset,
                window_size=window_size,
            )
        )

    replay = [record for record in records if record["label"] == "phase-c-replay"]
    # Add nine of the exact prior Phase-B windows. This makes the bounded
    # reliability decision cover 20 representative windows while retaining a
    # direct comparison against the previous M3 and Gemini artifacts.
    extra_records: list[dict[str, object]] = []
    source_by_id = {str(item["segment_id"]): item for item in items}
    prior_rows = baselines.get("gemini-3.7-flash", [])[:9]
    for index, prior_row in enumerate(prior_rows):
        prior_ids = prior_row.get("source_segment_ids", [])
        if not isinstance(prior_ids, list) or not prior_ids or any(str(sid) not in source_by_id for sid in prior_ids):
            continue
        extra_items = [source_by_id[str(sid)] for sid in prior_ids]
        extra_records.append(
            run_window(
                client=client,
                items=extra_items,
                baselines=baselines,
                label="phase-b-representative",
                offset=index,
                window_size=len(extra_items),
            )
        )
    records.extend(extra_records)
    representative = [
        record for record in records if record["label"] in {"phase-c-replay", "phase-b-representative"}
    ]
    summary = {
        "phase_c_replay_windows": len(replay),
        "additional_representative_windows": len(extra_records),
        "total_windows": len(representative),
        "valid_windows": sum(int(bool(record.get("valid"))) for record in representative),
        "output_limit_hits": sum(int(record.get("finish_reason") == "length" or record.get("error", {}).get("kind") == "output_limit") for record in representative),
        "transport_errors": sum(int(record.get("error", {}).get("kind") == "transient_exhausted") for record in representative),
        "max_latency_ms": max(int(record.get("latency_ms") or 0) for record in records),
    }
    report = {
        "schema": "m3-thinking-disabled-validation-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": MODEL,
        "source_job": "260815-20260816-152635-39ffbc",
        "source_file_sha256": source_hash,
        "source_segment_count": len(items),
        "selected_segment_count": 107,
        "thinking_mode": "disabled",
        "terminology_thinking_mode": "adaptive",
        "max_completion_tokens": 4096,
        "timeout_seconds": 60,
        "raw_immutable": True,
        "canary": canary,
        "records": records,
        "summary": summary,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"canary": canary, "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if summary["valid_windows"] == summary["total_windows"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
