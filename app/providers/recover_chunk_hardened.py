"""Recover one Chirp operation with provider-state checks and safe GCS cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from google.api_core import exceptions as google_exceptions
from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech

from app.live_features import words_to_text
from app.providers.chirp_chunk import has_speech
from app.providers.hardening_common import (
    PENDING_EXIT,
    RETRYABLE_EXIT,
    TERMINAL_EXIT,
    atomic_json,
    env_true,
    iso,
    parse_time,
    propagation_grace,
    provider_deadline,
    utcnow,
    window_matches,
)

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = DATA_DIR / "jobs" / JOB_NAME
DYNAMIC_BATCHING = env_true("CHIRP_DYNAMIC_BATCHING", default=False)
_TRANSIENT = (
    google_exceptions.TooManyRequests,
    google_exceptions.ServiceUnavailable,
    google_exceptions.DeadlineExceeded,
    google_exceptions.InternalServerError,
    google_exceptions.BadGateway,
    google_exceptions.GatewayTimeout,
)


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _elapsed_ms(start: object, end: object) -> int:
    first = parse_time(start)
    second = parse_time(end)
    if not first or not second:
        return 0
    return max(0, round((second - first).total_seconds() * 1000))


def _duration_ms(value: object) -> int:
    return round(value.total_seconds() * 1000)  # type: ignore[attr-defined]


def _operation_state(name: str) -> tuple[bool, int, str]:
    client = speech_v2.SpeechClient(
        client_options={"api_endpoint": "us-speech.googleapis.com"}
    )
    operation = client.transport.operations_client.get_operation(name)
    error = getattr(operation, "error", None)
    code = int(getattr(error, "code", 0) or 0)
    message = str(getattr(error, "message", "") or "")
    return bool(getattr(operation, "done", False)), code, message


def _operation_file_error(name: str) -> tuple[int, str] | None:
    """Read per-file errors embedded in a completed BatchRecognize response.

    Speech can complete the long-running operation while a file result still
    carries an error.  In that case no GCS JSON is written and polling for
    propagation forever only hides the provider failure.
    """
    client = speech_v2.SpeechClient(
        client_options={"api_endpoint": "us-speech.googleapis.com"}
    )
    try:
        operation = client.transport.operations_client.get_operation(name)
    except Exception:
        # The regular operation-state check remains the source of truth when
        # a second metadata fetch is temporarily unavailable.
        return None
    response_any = getattr(operation, "response", None)
    if response_any is None or not getattr(response_any, "type_url", ""):
        return None
    response = cloud_speech.BatchRecognizeResponse()
    try:
        if not response_any.Unpack(response):
            return None
    except (TypeError, ValueError):
        return None
    for result in response.results.values():
        error = getattr(result, "error", None)
        code = int(getattr(error, "code", 0) or 0)
        message = str(getattr(error, "message", "") or "")
        if code or message:
            return code or 13, message or "Speech batch file failed"
    return None


def _pending_or_terminal(prior: dict[str, Any], manifest_path: Path) -> int:
    now = utcnow()
    submitted = parse_time(prior.get("submitted_at") or prior.get("created_at"))
    age = now - submitted if submitted else None
    operation_name = str(prior.get("operation_name") or "")
    if operation_name:
        done, code, message = _operation_state(operation_name)
        prior["operation_status_checked_at"] = iso(now)
        prior["operation_done"] = done
        if done:
            file_error = _operation_file_error(operation_name)
            if file_error is not None:
                error_code, error_message = file_error
                prior.update(
                    status="FAILED",
                    error={
                        "code": "PROVIDER_FILE_ERROR",
                        "provider_code": error_code,
                        "message": error_message,
                    },
                    terminal_at=iso(now),
                )
                atomic_json(manifest_path, prior)
                print(
                    f"RECOVER=TERMINAL file_error code={error_code} "
                    f"message={error_message[:300]}"
                )
                return TERMINAL_EXIT
        if code:
            prior.update(
                status="FAILED",
                error={"code": code, "message": message or "Speech operation failed"},
                terminal_at=iso(now),
            )
            atomic_json(manifest_path, prior)
            print(
                f"RECOVER=TERMINAL operation_error code={code} "
                f"message={message[:300]}"
            )
            return TERMINAL_EXIT
        if done:
            done_at = parse_time(prior.get("operation_done_at"))
            if done_at is None:
                done_at = now
                prior["operation_done_at"] = iso(done_at)
            if now - done_at <= propagation_grace():
                prior.update(status="RECOVERING", last_recovery_check_at=iso(now))
                atomic_json(manifest_path, prior)
                print(
                    "RECOVER=RETRYABLE operation done; "
                    "waiting for GCS output propagation"
                )
                return RETRYABLE_EXIT
            prior.update(
                status="FAILED",
                error={
                    "code": "OUTPUT_MISSING",
                    "message": "Operation completed without a GCS result",
                },
                terminal_at=iso(now),
            )
            atomic_json(manifest_path, prior)
            print("RECOVER=TERMINAL operation completed without GCS output")
            return TERMINAL_EXIT
    if age is not None and age > provider_deadline():
        prior.update(
            status="FAILED",
            error={
                "code": "PROVIDER_DEADLINE_EXCEEDED",
                "message": (
                    "Chirp dynamic batch exceeded the configured provider deadline"
                ),
            },
            terminal_at=iso(now),
        )
        atomic_json(manifest_path, prior)
        print("RECOVER=TERMINAL provider deadline exceeded")
        return TERMINAL_EXIT
    prior.update(status="RECOVERING", last_recovery_check_at=iso(now))
    atomic_json(manifest_path, prior)
    print("RECOVER=PENDING provider operation is still running")
    return PENDING_EXIT


def _cleanup(
    bucket: Any,
    *,
    prior: dict[str, Any],
    result_blobs: list[Any],
) -> dict[str, Any]:
    if not env_true("CHIRP_GCS_CLEANUP_AFTER_RECOVERY", default=True):
        return {"status": "disabled", "deleted": []}
    deleted: list[str] = []
    errors: list[str] = []
    candidates = list(result_blobs)
    input_object = str(prior.get("input_object_name") or "")
    if input_object:
        candidates.append(bucket.blob(input_object))
    for blob in candidates:
        try:
            blob.delete()
            deleted.append(str(blob.name))
        except google_exceptions.NotFound:
            continue
        except Exception as exc:  # best effort after durable local evidence
            errors.append(type(exc).__name__)
    return {
        "status": "completed" if not errors else "pending",
        "deleted": deleted,
        "errors": errors,
        "checked_at": iso(),
    }


def main() -> int:
    index = int(os.environ.get("CHUNK_INDEX", "0"))
    start = float(os.environ.get("CHUNK_START_SECONDS", "0"))
    end = float(os.environ.get("CHUNK_END_SECONDS", "900"))
    name = f"chunk-{index:03d}"
    chunk = JOB / "chunks" / name
    manifest_path = chunk / "manifest.json"
    prior = _load(manifest_path)
    if not prior:
        print(f"RECOVER_{name}=TERMINAL missing manifest")
        return TERMINAL_EXIT
    if not window_matches(
        prior,
        start_seconds=start,
        end_seconds=end,
        dynamic_batching=DYNAMIC_BATCHING,
    ):
        prior.update(
            status="FAILED",
            error={
                "code": "INCOMPATIBLE_RETAINED_WINDOW",
                "message": (
                    "Retained operation does not match the current chunk "
                    "window or strategy"
                ),
            },
            terminal_at=iso(),
        )
        atomic_json(manifest_path, prior)
        print(f"RECOVER_{name}=TERMINAL incompatible retained operation")
        return TERMINAL_EXIT

    bucket = storage.Client().bucket(os.environ["GCS_BUCKET"])
    output_prefix = str(
        prior.get("output_prefix")
        or f"jobs/{JOB_NAME}/chunks/{name}/chirp-output/"
    )
    try:
        blobs = [
            blob
            for blob in bucket.list_blobs(prefix=output_prefix)
            if str(blob.name).lower().endswith(".json")
        ]
    except _TRANSIENT as exc:
        prior.update(
            status="RECOVERING",
            last_recovery_check_at=iso(),
            last_recovery_error={"type": type(exc).__name__, "retryable": True},
        )
        atomic_json(manifest_path, prior)
        print(f"RECOVER_{name}=RETRYABLE {type(exc).__name__}")
        return RETRYABLE_EXIT

    if not blobs:
        try:
            return _pending_or_terminal(prior, manifest_path)
        except _TRANSIENT as exc:
            prior.update(
                status="RECOVERING",
                last_recovery_check_at=iso(),
                last_recovery_error={
                    "type": type(exc).__name__,
                    "retryable": True,
                },
            )
            atomic_json(manifest_path, prior)
            print(f"RECOVER_{name}=RETRYABLE {type(exc).__name__}")
            return RETRYABLE_EXIT
    if len(blobs) != 1:
        prior.update(
            status="FAILED",
            error={
                "code": "AMBIGUOUS_GCS_OUTPUT",
                "message": f"Expected one JSON result object, found {len(blobs)}",
            },
            terminal_at=iso(),
        )
        atomic_json(manifest_path, prior)
        print(
            f"RECOVER_{name}=TERMINAL ambiguous GCS output count={len(blobs)}"
        )
        return TERMINAL_EXIT

    blob = blobs[0]
    provider_completed_at = iso(blob.updated) if blob.updated else iso()
    recovery_started_at = iso()
    try:
        raw = blob.download_as_text()
    except _TRANSIENT as exc:
        prior.update(
            status="RECOVERING",
            last_recovery_check_at=iso(),
            last_recovery_error={"type": type(exc).__name__, "retryable": True},
        )
        atomic_json(manifest_path, prior)
        print(f"RECOVER_{name}=RETRYABLE {type(exc).__name__}")
        return RETRYABLE_EXIT

    raw_temporary = chunk / "chirp-raw.json.tmp"
    raw_temporary.write_text(raw, encoding="utf-8")
    raw_temporary.replace(chunk / "chirp-raw.json")
    parsed = cloud_speech.BatchRecognizeResults.from_json(raw)
    offset = round(start * 1000)
    words: list[dict[str, object]] = []
    for result in parsed.results:
        if not result.alternatives:
            continue
        for word in result.alternatives[0].words:
            words.append(
                {
                    "word": word.word,
                    "start_ms": _duration_ms(word.start_offset) + offset,
                    "end_ms": _duration_ms(word.end_offset) + offset,
                }
            )

    audio = chunk / "audio.flac"
    if not words:
        if not audio.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start),
                    "-i",
                    str(JOB / "normalized.flac"),
                    "-t",
                    str(end - start),
                    "-c:a",
                    "flac",
                    str(audio),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        if has_speech(audio):
            prior.update(
                status="FAILED",
                error={
                    "code": "EMPTY_WITH_SPEECH",
                    "message": "No words for audible audio",
                },
                terminal_at=iso(),
            )
            atomic_json(manifest_path, prior)
            print(f"RECOVER_{name}=TERMINAL empty result for audible audio")
            return TERMINAL_EXIT
    status = "SUCCEEDED" if words else "EMPTY_SILENCE"
    recovered_at = iso()
    raw_text = words_to_text(words)
    atomic_json(
        chunk / "partial-transcript.json",
        {
            "chunkIndex": index,
            "sourceStartMs": offset,
            "sourceEndMs": round(end * 1000),
            "status": status,
            "wordCount": len(words),
            "rawText": raw_text,
            "firstWordMs": words[0]["start_ms"] if words else None,
            "lastWordMs": words[-1]["end_ms"] if words else None,
            "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "completedAt": recovered_at,
        },
    )
    atomic_json(chunk / "words.json", {"chunk_index": index, "words": words})

    # Persist a terminal local-success manifest before deleting any cloud object.
    prior.update(
        status=status,
        result_oneof="cloud_storage_result",
        output_field="gcs_prefix_recovery",
        gcs_uri=f"gs://{bucket.name}/{blob.name}",
        word_count=len(words),
        max_end_ms=max((int(word["end_ms"]) for word in words), default=0),
        provider_completed_at=provider_completed_at,
        operation_done_at=prior.get("operation_done_at") or provider_completed_at,
        recovery_started_at=recovery_started_at,
        recovered_at=recovered_at,
        provider_processing_ms=_elapsed_ms(
            prior.get("submitted_at"), provider_completed_at
        ),
        recovery_delay_ms=_elapsed_ms(
            provider_completed_at, recovery_started_at
        ),
        recovery_download_ms=_elapsed_ms(
            recovery_started_at, recovered_at
        ),
        total_wall_ms=_elapsed_ms(prior.get("submitted_at"), recovered_at),
        gcs_cleanup={"status": "pending", "deleted": []},
        last_recovery_error=None,
    )
    atomic_json(manifest_path, prior)

    cleanup = _cleanup(bucket, prior=prior, result_blobs=blobs)
    prior["gcs_cleanup"] = cleanup
    atomic_json(manifest_path, prior)
    audio.unlink(missing_ok=True)
    print(
        f"RECOVER_{name}=PASS status={status} words={len(words)} "
        f"cleanup={cleanup['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
