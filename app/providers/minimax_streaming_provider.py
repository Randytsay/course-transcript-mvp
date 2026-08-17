"""Opt-in strict Streaming 2.0 transport for MiniMax correction windows."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, Callable

from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import (
    MiniMaxCompletion,
    MiniMaxCorrectionClient,
    MiniMaxProviderError,
    _as_json_text,
    _failure_kind,
    _iso,
    _usage,
)
from app.providers.minimax_streaming import run_strict_stream


StreamRequest = Callable[[str, Mapping[str, str], bytes, float], dict[str, Any]]
_ALLOWED_CORRECTION_FIELDS = {"segment_id", "corrected_text", "uncertain_terms"}

# MiniMax provider-level codes documented by the CN error-code reference.
# HTTP status remains authoritative for 401/403/429/payment-style responses;
# these mappings disambiguate responses such as HTTP 422 that still carry a
# provider code telling us whether the failure is transient or permanent.
_PROVIDER_AUTH_CODES = {1004, 2049}
_PROVIDER_RATE_LIMIT_CODES = {1002, 1041, 2045}
_PROVIDER_USAGE_LIMIT_CODES = {1008, 2056}
_PROVIDER_TRANSIENT_CODES = {1000, 1001, 1013, 1024, 1033}
_PROVIDER_OUTPUT_LIMIT_CODES = {1039}
_PROVIDER_INVALID_CODES = {1026, 1027, 1042, 2013}


def _true(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _provider_error_code(result: Mapping[str, Any]) -> int | None:
    value = result.get("provider_error_code")
    try:
        code = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return code if code not in (None, 0) else None


def _provider_code_failure_kind(code: int | None) -> ProviderFailureKind | None:
    if code in _PROVIDER_AUTH_CODES:
        return ProviderFailureKind.AUTHENTICATION
    if code in _PROVIDER_RATE_LIMIT_CODES:
        return ProviderFailureKind.RATE_LIMIT
    if code in _PROVIDER_USAGE_LIMIT_CODES:
        return ProviderFailureKind.USAGE_LIMIT
    if code in _PROVIDER_TRANSIENT_CODES:
        return ProviderFailureKind.TRANSIENT_EXHAUSTED
    if code in _PROVIDER_OUTPUT_LIMIT_CODES:
        return ProviderFailureKind.OUTPUT_LIMIT
    if code in _PROVIDER_INVALID_CODES:
        return ProviderFailureKind.INVALID_RESPONSE
    return None


def _safe_failure_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep diagnostic metadata without retaining provider response text."""
    return {
        "streaming_v2": True,
        "deadline_exceeded": bool(result.get("deadline_exceeded")),
        "error_type": str(result.get("error_type") or ""),
        "provider_error_code": _provider_error_code(result),
        "provider_trace_id": result.get("provider_trace_id"),
        "provider_error_fingerprint": result.get("provider_error_fingerprint"),
        "provider_error_bytes": result.get("provider_error_bytes"),
    }


def _validate_streamed_correction_shape(content: str, items: list[dict[str, Any]]) -> None:
    """Reject reordered IDs and any model-emitted immutable/extra fields before acceptance."""
    try:
        payload = json.loads(_as_json_text(content))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MiniMaxProviderError(
            "MiniMax streaming response is not valid correction JSON",
            kind=ProviderFailureKind.INVALID_RESPONSE,
            raw_response={"streaming_v2": True, "shape_error": "invalid_json"},
        ) from exc
    if not isinstance(payload, Mapping) or set(payload) != {"segments"}:
        raise MiniMaxProviderError(
            "MiniMax streaming correction has unexpected top-level fields",
            kind=ProviderFailureKind.INVALID_RESPONSE,
            raw_response={"streaming_v2": True, "shape_error": "unexpected_top_level_fields"},
        )
    received = payload.get("segments")
    if not isinstance(received, list) or len(received) != len(items):
        raise MiniMaxProviderError(
            "MiniMax streaming correction has the wrong segment count",
            kind=ProviderFailureKind.INVALID_RESPONSE,
            raw_response={"streaming_v2": True, "shape_error": "segment_count"},
        )
    expected_ids = [str(item["segment_id"]) for item in items]
    observed_ids: list[str] = []
    for entry in received:
        if not isinstance(entry, Mapping):
            raise MiniMaxProviderError(
                "MiniMax streaming correction segment is not an object",
                kind=ProviderFailureKind.INVALID_RESPONSE,
                raw_response={"streaming_v2": True, "shape_error": "segment_not_object"},
            )
        keys = {str(key) for key in entry}
        unexpected = sorted(keys - _ALLOWED_CORRECTION_FIELDS)
        if unexpected:
            raise MiniMaxProviderError(
                "MiniMax streaming correction emitted forbidden/immutable fields",
                kind=ProviderFailureKind.INVALID_RESPONSE,
                raw_response={
                    "streaming_v2": True,
                    "shape_error": "forbidden_fields",
                    "forbidden_fields": unexpected,
                },
            )
        if "segment_id" not in entry or "corrected_text" not in entry:
            raise MiniMaxProviderError(
                "MiniMax streaming correction is missing required fields",
                kind=ProviderFailureKind.INVALID_RESPONSE,
                raw_response={"streaming_v2": True, "shape_error": "missing_required_fields"},
            )
        observed_ids.append(str(entry.get("segment_id")))
        if not isinstance(entry.get("corrected_text"), str):
            raise MiniMaxProviderError(
                "MiniMax streaming corrected_text is not a string",
                kind=ProviderFailureKind.INVALID_RESPONSE,
                raw_response={"streaming_v2": True, "shape_error": "corrected_text_type"},
            )
        if "uncertain_terms" in entry and not isinstance(entry.get("uncertain_terms"), list):
            raise MiniMaxProviderError(
                "MiniMax streaming uncertain_terms is not a list",
                kind=ProviderFailureKind.INVALID_RESPONSE,
                raw_response={"streaming_v2": True, "shape_error": "uncertain_terms_type"},
            )
    if observed_ids != expected_ids:
        raise MiniMaxProviderError(
            "MiniMax streaming correction changed or reordered segment IDs",
            kind=ProviderFailureKind.INVALID_RESPONSE,
            raw_response={
                "streaming_v2": True,
                "shape_error": "segment_id_order",
                "expected_count": len(expected_ids),
                "observed_count": len(observed_ids),
            },
        )


class MiniMaxStreamingCorrectionClient(MiniMaxCorrectionClient):
    """MiniMax client that keeps terminology non-stream and streams correction only."""

    def __init__(self, *args: Any, stream_request: StreamRequest | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.streaming_enabled = _true("MINIMAX_M3_STREAMING_ENABLED", False)
        self.stream_deadline_seconds = max(
            1.0,
            float(os.getenv("MINIMAX_M3_STREAM_DEADLINE_SECONDS", "75")),
        )
        self.stream_request = stream_request or run_strict_stream

    def _stream_failure_kind(self, result: Mapping[str, Any]) -> ProviderFailureKind:
        status = result.get("status_code")
        try:
            status_code = int(status) if status is not None else 0
        except (TypeError, ValueError):
            status_code = 0
        error_type = str(result.get("error_type") or "").strip()
        error_payload = result.get("error_payload")

        # Preserve HTTP-layer fail-closed semantics first. Provider-level codes
        # then disambiguate HTTP 422 and similar wrapper statuses.
        if status_code in {401, 403}:
            return ProviderFailureKind.AUTHENTICATION
        if status_code == 429:
            return ProviderFailureKind.RATE_LIMIT
        if status_code in {402, 409, 413}:
            return ProviderFailureKind.USAGE_LIMIT
        provider_kind = _provider_code_failure_kind(_provider_error_code(result))
        if provider_kind is not None:
            return provider_kind
        if error_type == "http_error":
            return _failure_kind(status_code, error_payload)
        if error_type in {"ValueError", "UnicodeError"}:
            return ProviderFailureKind.INVALID_RESPONSE
        return ProviderFailureKind.TRANSIENT_EXHAUSTED

    def _request(
        self,
        prompt: str,
        items: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        thinking_mode: str | None = None,
    ) -> MiniMaxCompletion:
        # extract_terms() always supplies an explicit thinking_mode; preserve the
        # already-validated non-stream terminology path. Streaming 2.0 is correction-only.
        if not self.streaming_enabled or thinking_mode is not None:
            return super()._request(
                prompt,
                items,
                system_prompt=system_prompt,
                thinking_mode=thinking_mode,
            )

        key = self._key()
        request_payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or (
                        "Correct Traditional-Chinese ASR text only. Chirp 3 is the immutable "
                        "source of segment IDs, order, and timestamps. Do not summarize, add, "
                        "split, merge, reorder, or alter IDs. Return JSON only with exactly "
                        '{"segments":[{"segment_id":"...","corrected_text":"...",'
                        '"uncertain_terms":[...]}]}.'
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0,
            "thinking": {"type": self.correction_thinking_mode},
            "max_completion_tokens": self.max_output_tokens,
            "reasoning_split": self.reasoning_split,
        }
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "x-api-key": key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        attempts: list[dict[str, Any]] = []
        last_error: MiniMaxProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            started_at = _iso()
            result: dict[str, Any] = {}
            try:
                result = self.stream_request(
                    self.url,
                    headers,
                    body,
                    self.stream_deadline_seconds,
                )
                status_value = result.get("status_code")
                try:
                    status = int(status_value) if status_value is not None else 0
                except (TypeError, ValueError):
                    status = 0
                if not bool(result.get("ok")):
                    kind = self._stream_failure_kind(result)
                    raise MiniMaxProviderError(
                        "MiniMax streaming transport failed",
                        kind=kind,
                        status_code=status or None,
                        raw_response=_safe_failure_metadata(result),
                    )

                finish_reason = str(result.get("finish_reason") or "").strip().lower() or None
                if finish_reason == "length":
                    raise MiniMaxProviderError(
                        "MiniMax streaming generation reached the configured output limit",
                        kind=ProviderFailureKind.OUTPUT_LIMIT,
                        status_code=status or 200,
                        raw_response={"streaming_v2": True, "finish_reason": finish_reason},
                    )
                if finish_reason != "stop":
                    raise MiniMaxProviderError(
                        "MiniMax streaming response did not finish with stop",
                        kind=ProviderFailureKind.INVALID_RESPONSE,
                        status_code=status or 200,
                        raw_response={"streaming_v2": True, "finish_reason": finish_reason},
                    )
                raw_usage = result.get("usage")
                if not isinstance(raw_usage, Mapping) or not raw_usage:
                    raise MiniMaxProviderError(
                        "MiniMax streaming response is missing usage metadata",
                        kind=ProviderFailureKind.INVALID_RESPONSE,
                        status_code=status or 200,
                        raw_response={"streaming_v2": True, "usage_available": False},
                    )
                content = result.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise MiniMaxProviderError(
                        "MiniMax streaming response is missing final content",
                        kind=ProviderFailureKind.INVALID_RESPONSE,
                        status_code=status or 200,
                        raw_response={"streaming_v2": True, "finish_reason": finish_reason},
                    )
                _validate_streamed_correction_shape(content, items)

                latency_ms = int(result.get("latency_ms") or round((time.monotonic() - started) * 1000))
                attempt_record = {
                    "attempt": attempt,
                    "started_at": started_at,
                    "completed_at": _iso(),
                    "latency_ms": latency_ms,
                    "status_code": status or 200,
                    "finish_reason": finish_reason,
                    "failure_kind": None,
                    "transport": "streaming_v2",
                    "stream_first_event_ms": result.get("first_event_ms"),
                    "stream_event_count": int(result.get("event_count") or 0),
                    "stream_done_seen": bool(result.get("done_seen")),
                    "stream_deadline_seconds": self.stream_deadline_seconds,
                    "stream_usage_available": True,
                    "provider_error_code": None,
                    "provider_trace_id": None,
                }
                attempts.append(attempt_record)
                self._last_attempts = attempts
                usage = _usage({"usage": dict(raw_usage)})
                raw_payload = {
                    "streaming_v2": True,
                    "finish_reason": finish_reason,
                    "usage_available": True,
                    "first_event_ms": result.get("first_event_ms"),
                    "event_count": int(result.get("event_count") or 0),
                    "done_seen": bool(result.get("done_seen")),
                    "deadline_seconds": self.stream_deadline_seconds,
                }
                return MiniMaxCompletion(content, usage, raw_payload, status or 200, attempts, finish_reason)
            except MiniMaxProviderError as exc:
                last_error = exc
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started_at,
                        "completed_at": _iso(),
                        "latency_ms": int(result.get("latency_ms") or round((time.monotonic() - started) * 1000)),
                        "status_code": result.get("status_code") or exc.status_code,
                        "finish_reason": result.get("finish_reason"),
                        "failure_kind": exc.kind.value,
                        "transport": "streaming_v2",
                        "stream_first_event_ms": result.get("first_event_ms"),
                        "stream_event_count": int(result.get("event_count") or 0),
                        "stream_done_seen": bool(result.get("done_seen")),
                        "stream_deadline_seconds": self.stream_deadline_seconds,
                        "stream_deadline_exceeded": bool(result.get("deadline_exceeded")),
                        "stream_usage_available": isinstance(result.get("usage"), Mapping),
                        "provider_error_code": _provider_error_code(result),
                        "provider_trace_id": result.get("provider_trace_id"),
                        "provider_error_fingerprint": result.get("provider_error_fingerprint"),
                        "provider_error_bytes": result.get("provider_error_bytes"),
                    }
                )
                self._last_attempts = attempts
                if exc.kind in {
                    ProviderFailureKind.AUTHENTICATION,
                    ProviderFailureKind.USAGE_LIMIT,
                    ProviderFailureKind.INVALID_RESPONSE,
                    ProviderFailureKind.OUTPUT_LIMIT,
                }:
                    raise
                if attempt < self.max_attempts:
                    self.sleeper(min(30.0, 2**attempt))

        self._last_attempts = attempts
        assert last_error is not None
        raise MiniMaxProviderError(
            "MiniMax streaming request failed after bounded retries",
            kind=(
                ProviderFailureKind.TRANSIENT_EXHAUSTED
                if last_error.kind in {ProviderFailureKind.RATE_LIMIT, ProviderFailureKind.TRANSIENT_EXHAUSTED}
                else last_error.kind
            ),
            status_code=last_error.status_code,
            raw_response=last_error.raw_response,
        )
