"""Strict MiniMax OpenAI-compatible SSE transport with a hard wall-clock deadline.

The child process owns all partial provider output. The parent receives content only after
the stream has terminated normally, so deadline/transport failures cannot leak partial
correction text into the caller.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


StreamWorker = Callable[[Any, str, Mapping[str, str], bytes, float], None]


def parse_sse_lines(lines: Iterable[bytes | str]) -> dict[str, Any]:
    """Parse OpenAI-compatible SSE events into a final, non-partial aggregate."""
    content_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    event_count = 0
    done_seen = False

    for raw_line in lines:
        text = raw_line.decode("utf-8", "strict") if isinstance(raw_line, bytes) else str(raw_line)
        text = text.strip()
        if not text or text.startswith(":"):
            continue
        if not text.startswith("data:"):
            raise ValueError("malformed_sse_event")
        data = text[5:].strip()
        if data == "[DONE]":
            done_seen = True
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed_sse_json") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("sse_payload_not_object")
        event_count += 1

        raw_usage = payload.get("usage")
        if isinstance(raw_usage, Mapping):
            usage = dict(raw_usage)

        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            raw_finish = choice.get("finish_reason")
            if raw_finish not in (None, ""):
                finish_reason = str(raw_finish).strip().lower() or None
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                part = delta.get("content")
                if isinstance(part, str):
                    content_parts.append(part)
                elif isinstance(part, list):
                    content_parts.extend(
                        str(item.get("text", "")) if isinstance(item, Mapping) else str(item)
                        for item in part
                    )
            message = choice.get("message")
            if isinstance(message, Mapping):
                part = message.get("content")
                if isinstance(part, str):
                    content_parts.append(part)

    return {
        "content": "".join(content_parts),
        "finish_reason": finish_reason,
        "usage": usage,
        "event_count": event_count,
        "done_seen": done_seen,
    }


def _safe_error_body(raw: bytes) -> str:
    text = raw[:2000].decode("utf-8", "replace")
    lowered = text.lower()
    for marker in ("authorization", "bearer ", "api_key", "api key", "secret", "password"):
        if marker in lowered:
            return "[REDACTED_PROVIDER_ERROR]"
    return text


def _streaming_child(
    connection: Any,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    socket_timeout: float,
) -> None:
    started = time.monotonic()
    first_event_ms: int | None = None
    try:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        with urlopen(request, timeout=socket_timeout) as response:
            status = int(response.status)
            lines: list[bytes] = []
            for line in response:
                if first_event_ms is None and line.strip().startswith(b"data:"):
                    first_event_ms = round((time.monotonic() - started) * 1000)
                lines.append(line)
            parsed = parse_sse_lines(lines)
            connection.send(
                {
                    "ok": 200 <= status < 300,
                    "status_code": status,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "first_event_ms": first_event_ms,
                    **parsed,
                }
            )
    except HTTPError as exc:
        try:
            raw = exc.read()
        except OSError:
            raw = b""
        try:
            connection.send(
                {
                    "ok": False,
                    "status_code": int(exc.code),
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "first_event_ms": first_event_ms,
                    "error_type": "http_error",
                    "error_payload": _safe_error_body(raw),
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    except (URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
        try:
            connection.send(
                {
                    "ok": False,
                    "status_code": None,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "first_event_ms": first_event_ms,
                    "error_type": type(exc).__name__,
                    "error_payload": str(exc)[-500:],
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def run_strict_stream(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    deadline_seconds: float,
    *,
    worker: StreamWorker = _streaming_child,
    start_method: str = "spawn",
) -> dict[str, Any]:
    """Run one SSE request with a true parent-enforced wall-clock deadline."""
    deadline_seconds = max(1.0, float(deadline_seconds))
    socket_timeout = deadline_seconds + 5.0
    try:
        context = mp.get_context(start_method)
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=worker,
            args=(child, url, dict(headers), body, socket_timeout),
            daemon=True,
        )
        started = time.monotonic()
        process.start()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "status_code": None,
            "deadline_exceeded": False,
            "latency_ms": 0,
            "error_type": "process_start_failure",
            "error_payload": str(exc)[-500:],
        }

    child.close()
    result: dict[str, Any] | None = None
    try:
        absolute_deadline = started + deadline_seconds
        while True:
            remaining = absolute_deadline - time.monotonic()
            if remaining <= 0:
                break
            if parent.poll(min(0.1, remaining)):
                try:
                    received = parent.recv()
                except EOFError:
                    received = {"ok": False, "error_type": "child_pipe_eof"}
                if isinstance(received, Mapping):
                    result = dict(received)
                else:
                    result = {"ok": False, "error_type": "invalid_child_result"}
                break
            if not process.is_alive():
                break
        if result is None:
            deadline_exceeded = time.monotonic() >= absolute_deadline
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.0)
            return {
                "ok": False,
                "status_code": None,
                "deadline_exceeded": deadline_exceeded,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error_type": "wall_clock_deadline" if deadline_exceeded else "child_exit_without_result",
            }
        process.join(timeout=1.0)
        result.setdefault("deadline_exceeded", False)
        result.setdefault("latency_ms", round((time.monotonic() - started) * 1000))
        return result
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
