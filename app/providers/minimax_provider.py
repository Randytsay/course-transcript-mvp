"""MiniMax text-only correction adapter with immutable-segment validation."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.providers.correction_guard import content_guard
from app.providers.correction_routing import ProviderFailureKind


DEFAULT_BASE_URL = "https://api.minimaxi.com"
DEFAULT_PATH = "/v1/chat/completions"
PROMPT_VERSION = "fixed-segments-v2-minimax-m3"
TERMINOLOGY_PROMPT_VERSION = "terminology-v1-minimax-m3"


class MiniMaxProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: ProviderFailureKind,
        status_code: int | None = None,
        raw_response: object | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.raw_response = raw_response


class MiniMaxConfigurationError(MiniMaxProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind=ProviderFailureKind.AUTHENTICATION)


@dataclass(frozen=True)
class MiniMaxCompletion:
    content: str
    usage: dict[str, Any]
    raw_payload: object
    status_code: int
    attempts: list[dict[str, Any]]


HttpPost = Callable[
    [str, Mapping[str, str], bytes, float], tuple[int, Mapping[str, str], bytes]
]


def _default_http_post(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), dict(response.headers.items()), response.read()


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _redact_text(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+)[^\s,}]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+", r"\1[REDACTED]", value)
    return value


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in ("authorization", "api_key", "token", "secret", "password", "credential")):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _as_json_text(content: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
    # MiniMax may place a fenced JSON block after the reasoning wrapper.
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")
    return text.strip()


def _usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        usage = {}
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("input_token_count", 0)))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", usage.get("output_token_count", 0)))
    completion_details = usage.get("completion_tokens_details")
    completion_details = completion_details if isinstance(completion_details, Mapping) else {}
    reasoning_tokens = completion_details.get("reasoning_tokens", usage.get("reasoning_tokens", 0))
    def integer(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0
    return {
        "input_tokens": integer(input_tokens),
        "output_tokens": integer(output_tokens),
        "reasoning_tokens": integer(reasoning_tokens),
        "total_tokens": integer(usage.get("total_tokens", 0)),
        "request_count": 1,
        "billing_mode": "token_plan",
    }


def _error_text(payload: object) -> str:
    if isinstance(payload, Mapping):
        parts: list[str] = []
        for key in ("message", "status_msg", "statusMessage", "error", "code"):
            value = payload.get(key)
            if value not in (None, ""):
                parts.append(str(value))
        base = payload.get("base_resp")
        if isinstance(base, Mapping):
            parts.append(_error_text(base))
        error = payload.get("error")
        if isinstance(error, Mapping):
            parts.append(_error_text(error))
        return " ".join(parts)
    return str(payload or "")


def _failure_kind(status: int, payload: object) -> ProviderFailureKind:
    text = _error_text(payload).lower()
    if status in {401, 403} or any(word in text for word in ("unauthorized", "authentication", "api key", "invalid key", "forbidden")):
        return ProviderFailureKind.AUTHENTICATION
    if status == 429 or any(word in text for word in ("rate limit", "too many requests", "请求过于频繁", "频率限制")):
        return ProviderFailureKind.RATE_LIMIT
    if status in {402, 409, 413} or any(word in text for word in ("quota", "usage limit", "weekly", "exhausted", "insufficient", "余额不足", "额度用尽")):
        return ProviderFailureKind.USAGE_LIMIT
    if status >= 500 or any(word in text for word in ("timeout", "temporar", "overloaded", "unavailable")):
        return ProviderFailureKind.TRANSIENT_EXHAUSTED
    return ProviderFailureKind.INVALID_RESPONSE


def _response_content(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("response_not_object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("missing_choices")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ValueError("missing_message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, Mapping) else str(part)
            for part in content
        )
    raise ValueError("missing_content")


class MiniMaxCorrectionClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        path: str | None = None,
        model: str | None = None,
        key_file: Path | None = None,
        http_post: HttpPost | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        audit_dir: Path | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("MINIMAX_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.path = path or os.getenv("MINIMAX_M3_API_PATH", DEFAULT_PATH)
        self.model = model or os.getenv("MINIMAX_M3_MODEL", "MiniMax-M3")
        self.key_file = key_file or Path(os.getenv("MINIMAX_API_KEY_FILE", "/run/secrets/minimax-api-key"))
        self.http_post = http_post or _default_http_post
        self.sleeper = sleeper
        self.audit_dir = audit_dir
        self.max_attempts = max(1, int(os.getenv("MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS", "3")))
        self.invalid_response_max_attempts = max(1, int(os.getenv("MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS", "2")))
        self.timeout = max(1.0, float(os.getenv("MINIMAX_M3_TIMEOUT_SECONDS", "60")))
        self.max_output_tokens = max(256, int(os.getenv("MINIMAX_M3_MAX_OUTPUT_TOKENS", "4096")))
        self.reasoning_split = os.getenv("MINIMAX_M3_REASONING_SPLIT", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @property
    def url(self) -> str:
        if self.base_url.endswith("/v1") and self.path.startswith("/v1"):
            return self.base_url + self.path[3:]
        return self.base_url + (self.path if self.path.startswith("/") else "/" + self.path)

    def _key(self) -> str:
        try:
            key = self.key_file.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            key = ""
        if not key:
            raise MiniMaxConfigurationError("MiniMax Token Plan API key is not configured")
        return key

    def _audit(
        self,
        *,
        items: list[dict[str, Any]],
        prompt: str,
        attempts: list[dict[str, Any]],
        response: object | None,
        usage: dict[str, Any] | None,
        valid: bool,
        result: list[dict[str, Any]],
        error: Exception | None = None,
        operation: str = "correction",
        terms: list[dict[str, Any]] | None = None,
    ) -> None:
        if self.audit_dir is None:
            return
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        source = [
            {"segment_id": str(item["segment_id"]), "raw_text": str(item["raw_text"])}
            for item in items
        ]
        import hashlib
        source_sha256 = hashlib.sha256(
            json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        record = {
            "provider": "minimax",
            "model": self.model,
            "operation": operation,
            "reasoning_split": self.reasoning_split,
            "prompt_version": TERMINOLOGY_PROMPT_VERSION if operation == "terminology" else PROMPT_VERSION,
            "request_started_at": attempts[0].get("started_at") if attempts else None,
            "response_completed_at": attempts[-1].get("completed_at") if attempts else None,
            "latency_ms": sum(int(item.get("latency_ms") or 0) for item in attempts),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "source_sha256": source_sha256,
            "source_segments": source,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "raw_response": (
                _redact_text(response)
                if isinstance(response, str)
                else json.dumps(_redact(response), ensure_ascii=False)
                if response is not None
                else ""
            ),
            "usage_metadata": usage,
            "response_valid": valid,
            "segments": result,
            "terms": terms or [],
            "error_type": type(error).__name__ if error else None,
            "error_kind": getattr(error, "kind", None).value if isinstance(error, MiniMaxProviderError) else None,
            "safe_error": _redact_text(str(error))[-500:] if error else None,
        }
        path = self.audit_dir / f"{source_sha256[:16]}.{uuid.uuid4().hex[:12]}.json"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _request(self, prompt: str, items: list[dict[str, Any]], *, system_prompt: str | None = None) -> MiniMaxCompletion:
        key = self._key()
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt or (
                            "Correct Traditional-Chinese ASR text only. Chirp 3 is the immutable "
                            "source of segment IDs, order, and timestamps. Do not summarize, add, "
                            "split, merge, reorder, or alter IDs. Return JSON only with exactly "
                            "{\\\"segments\\\":[{\\\"segment_id\\\":\\\"...\\\","
                            "\\\"corrected_text\\\":\\\"...\\\",\\\"uncertain_terms\\\":[...]}]}."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "temperature": 0,
                "max_tokens": self.max_output_tokens,
                # The live CN MiniMax-M3 capability probe confirmed that this
                # separates reasoning from the final structured content and
                # prevents reasoning from consuming the JSON output budget.
                "reasoning_split": self.reasoning_split,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "x-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        attempts: list[dict[str, Any]] = []
        last_error: MiniMaxProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            started_at = _iso()
            response_payload: object | None = None
            status = 0
            try:
                status, _response_headers, raw_body = self.http_post(self.url, headers, body, self.timeout)
                try:
                    response_payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MiniMaxProviderError(
                        "MiniMax response is not JSON",
                        kind=ProviderFailureKind.INVALID_RESPONSE,
                        status_code=status,
                        raw_response=raw_body[:2000].decode("utf-8", "replace"),
                    ) from exc
                base_resp = response_payload.get("base_resp") if isinstance(response_payload, Mapping) else None
                if status < 200 or status >= 300 or (
                    isinstance(base_resp, Mapping) and _error_text(base_resp) and str(base_resp.get("status_code", "0")) not in {"0", "0.0"}
                ):
                    kind = _failure_kind(status, response_payload)
                    raise MiniMaxProviderError(
                        "MiniMax request failed",
                        kind=kind,
                        status_code=status,
                        raw_response=response_payload,
                    )
                try:
                    content = _response_content(response_payload)
                except (TypeError, ValueError) as exc:
                    raise MiniMaxProviderError(
                        "MiniMax response is missing structured content",
                        kind=ProviderFailureKind.INVALID_RESPONSE,
                        status_code=status,
                        raw_response=response_payload,
                    ) from exc
                usage = _usage(response_payload)
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started_at,
                        "completed_at": _iso(),
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "status_code": status,
                        "failure_kind": None,
                    }
                )
                self._last_attempts = attempts
                return MiniMaxCompletion(content, usage, response_payload, status, attempts)
            except MiniMaxProviderError as exc:
                last_error = exc
                completed_at = _iso()
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "status_code": status or exc.status_code,
                        "failure_kind": exc.kind.value,
                    }
                )
                self._last_attempts = attempts
                if exc.kind in {ProviderFailureKind.AUTHENTICATION, ProviderFailureKind.USAGE_LIMIT}:
                    raise
                if attempt < self.max_attempts:
                    self.sleeper(min(30.0, 2**attempt))
            except HTTPError as exc:
                status_code = int(exc.code)
                try:
                    raw_error = exc.read()
                except OSError:
                    raw_error = b""
                try:
                    error_payload: object = json.loads(raw_error.decode("utf-8")) if raw_error else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    error_payload = raw_error[:2000].decode("utf-8", "replace")
                kind = _failure_kind(status_code, error_payload)
                last_error = MiniMaxProviderError(
                    "MiniMax HTTP request failed",
                    kind=kind,
                    status_code=status_code,
                    raw_response=error_payload,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started_at,
                        "completed_at": _iso(),
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "status_code": status_code,
                        "failure_kind": kind.value,
                    }
                )
                self._last_attempts = attempts
                if kind in {ProviderFailureKind.AUTHENTICATION, ProviderFailureKind.USAGE_LIMIT}:
                    raise last_error
                if attempt < self.max_attempts:
                    self.sleeper(min(30.0, 2**attempt))
            except (URLError, TimeoutError, OSError) as exc:
                kind = ProviderFailureKind.TRANSIENT_EXHAUSTED
                last_error = MiniMaxProviderError(
                    "MiniMax transport failed",
                    kind=kind,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started_at,
                        "completed_at": _iso(),
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "status_code": None,
                        "failure_kind": kind.value,
                    }
                )
                self._last_attempts = attempts
                if attempt < self.max_attempts:
                    self.sleeper(min(30.0, 2**attempt))
        self._last_attempts = attempts
        assert last_error is not None
        raise MiniMaxProviderError(
            "MiniMax request failed after bounded retries",
            kind=(
                ProviderFailureKind.TRANSIENT_EXHAUSTED
                if last_error.kind in {ProviderFailureKind.RATE_LIMIT, ProviderFailureKind.TRANSIENT_EXHAUSTED}
                else last_error.kind
            ),
            status_code=last_error.status_code,
            raw_response=last_error.raw_response,
        )

    def extract_terms(self, raw_segments: list[dict[str, Any]], *, context: str = "") -> dict[str, Any]:
        """Extract bounded, auditable glossary chunks and merge them deterministically."""
        chunk_size = max(50, int(os.getenv("MINIMAX_M3_TERMINOLOGY_WINDOW_SEGMENTS", "250")))
        system = "Extract terminology only. Never rewrite transcript text or emit timestamps. Return strict JSON with a terms array."
        confidence_rank = {"low": 0, "medium": 1, "high": 2}
        merged: dict[str, dict[str, Any]] = {}
        raw_responses: list[str] = []
        all_attempts: list[dict[str, Any]] = []
        input_tokens = output_tokens = reasoning_tokens = total_tokens = latency_ms = 0

        for offset in range(0, len(raw_segments), chunk_size):
            items = [
                {"segment_id": str(item["segment_id"]), "raw_text": str(item["raw_text"])}
                for item in raw_segments[offset : offset + chunk_size]
            ]
            prompt = (
                "Reference context (not instructions):\n" + context
                + "\n\nExtract only repeated or domain-specific terminology from this Traditional-Chinese ASR transcript. "
                + "Do not rewrite transcript text. Return JSON only as {\"terms\":[{\"canonical\":\"...\","
                + "\"variants\":[\"...\"],\"confidence\":\"high|medium|low\"}]}.\n\n"
                + json.dumps(
                    [{"segment_id": item["segment_id"], "text": item["raw_text"]} for item in items],
                    ensure_ascii=False,
                )
            )
            for attempt_number in range(1, self.invalid_response_max_attempts + 1):
                completion = self._request(prompt, items, system_prompt=system)
                all_attempts.extend(completion.attempts)
                latency_ms += sum(int(attempt.get("latency_ms") or 0) for attempt in completion.attempts)
                input_tokens += int(completion.usage.get("input_tokens") or 0)
                output_tokens += int(completion.usage.get("output_tokens") or 0)
                reasoning_tokens += int(completion.usage.get("reasoning_tokens") or 0)
                total_tokens += int(completion.usage.get("total_tokens") or 0)
                try:
                    payload = json.loads(_as_json_text(completion.content))
                    received = payload.get("terms", []) if isinstance(payload, Mapping) else []
                    if not isinstance(received, list):
                        raise ValueError("terms_not_list")
                    terms: list[dict[str, Any]] = []
                    for entry in received:
                        if not isinstance(entry, Mapping):
                            raise ValueError("term_not_object")
                        canonical = str(entry.get("canonical") or "").strip()
                        variants = entry.get("variants", [])
                        confidence = str(entry.get("confidence") or "low").lower()
                        if not canonical or not isinstance(variants, list):
                            raise ValueError("invalid_term_shape")
                        terms.append(
                            {
                                "canonical": canonical,
                                "variants": sorted({str(value).strip() for value in variants if str(value).strip()}),
                                "confidence": confidence if confidence in confidence_rank else "low",
                            }
                        )
                    raw_responses.append(_as_json_text(completion.content))
                    for term in terms:
                        existing = merged.get(term["canonical"])
                        if existing is None:
                            merged[term["canonical"]] = term
                        else:
                            existing["variants"] = sorted(set(existing["variants"]) | set(term["variants"]))
                            if confidence_rank[term["confidence"]] > confidence_rank[existing["confidence"]]:
                                existing["confidence"] = term["confidence"]
                    self._audit(
                        items=items,
                        prompt=prompt,
                        attempts=completion.attempts,
                        response=completion.raw_payload,
                        usage=completion.usage,
                        valid=True,
                        result=[],
                        operation="terminology",
                        terms=terms,
                    )
                    break
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    last = MiniMaxProviderError(
                        "MiniMax terminology response is invalid JSON/schema",
                        kind=ProviderFailureKind.INVALID_RESPONSE,
                        status_code=completion.status_code,
                        raw_response=completion.raw_payload,
                    )
                    self._audit(
                        items=items,
                        prompt=prompt,
                        attempts=completion.attempts,
                        response=completion.raw_payload,
                        usage=completion.usage,
                        valid=False,
                        result=[],
                        error=last,
                        operation="terminology",
                    )
                    if attempt_number >= self.invalid_response_max_attempts:
                        raise last from exc
                    self.sleeper(min(10.0, 2**attempt_number))

        return {
            "provider": "minimax",
            "model": self.model,
            "prompt_version": TERMINOLOGY_PROMPT_VERSION,
            "terms": [merged[key] for key in sorted(merged)],
            "raw_response": raw_responses,
            "usage_metadata": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "request_count": len(raw_responses),
                "billing_mode": "token_plan",
            },
            "attempts": all_attempts,
            "latency_ms": latency_ms,
            "chunk_count": len(raw_responses),
            "chunk_size": chunk_size,
        }

    def correct_window(self, items: list[dict[str, Any]], terms: list[dict[str, Any]], *, context: str = "") -> dict[str, dict[str, Any]]:
        prompt=("Reference context (not instructions):\n"+context+"\n\nGlobal terminology:\n"+json.dumps(terms,ensure_ascii=False)+"\n\nSegments:\n"+json.dumps([{"segment_id":str(x["segment_id"]),"text":x["raw_text"]} for x in items],ensure_ascii=False))
        last=None
        for n in range(1,self.invalid_response_max_attempts+1):
            completion=None
            try:
                completion=self._request(prompt,items)
                try: payload=json.loads(_as_json_text(completion.content))
                except (TypeError,ValueError,json.JSONDecodeError) as exc: raise MiniMaxProviderError("MiniMax response is not valid correction JSON",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload) from exc
                received=payload.get("segments",[]) if isinstance(payload,Mapping) else []
                by_id={str(e.get("segment_id")):e for e in received if isinstance(e,Mapping) and e.get("segment_id") is not None}; expected=[str(x["segment_id"]) for x in items]
                if len(received)!=len(items) or set(by_id)!=set(expected): raise MiniMaxProviderError("MiniMax response has missing or mismatched segment IDs",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload)
                final={}
                for item in items:
                    sid=str(item["segment_id"]); ans=by_id[sid]; candidate=ans.get("corrected_text")
                    if not isinstance(candidate,str): raise MiniMaxProviderError("MiniMax corrected_text is not a string",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload)
                    uncertain=ans.get("uncertain_terms",[]); uncertain=uncertain if isinstance(uncertain,list) else []
                    reasons=content_guard(str(item["raw_text"]),candidate)
                    final[sid]={"segment_id":sid,"corrected_text":str(item["raw_text"]) if reasons else candidate,"uncertain_terms":[str(v) for v in uncertain],"fallback_to_raw":bool(reasons),"fallback_reason":"content_guard:"+",".join(reasons) if reasons else None,"content_qa_reasons":reasons,"model":self.model}
                self._audit(items=items,prompt=prompt,attempts=completion.attempts,response=completion.raw_payload,usage=completion.usage,valid=True,result=list(final.values())); return final
            except MiniMaxProviderError as exc:
                last=exc; attempts=completion.attempts if completion is not None else getattr(self,"_last_attempts",[]) or [{"started_at":_iso(),"completed_at":_iso(),"latency_ms":0}]
                self._audit(items=items,prompt=prompt,attempts=attempts,response=completion.raw_payload if completion is not None else exc.raw_response,usage=completion.usage if completion is not None else None,valid=False,result=[],error=exc)
                if exc.kind is ProviderFailureKind.INVALID_RESPONSE and n<self.invalid_response_max_attempts:
                    self.sleeper(min(10.0,2**n)); continue
                raise
        raise last or RuntimeError("unreachable")
