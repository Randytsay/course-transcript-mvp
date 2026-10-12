"""Golden V2 optional third recognizer: Gemini 3.5 Transcribe on Vertex AI.

Independent evidence only. Requires per-window cost authorization from the
durable worker; disabled by default, never changes Chirp source timing.
The Vertex/Agent Platform conservative request cap is 15 minutes, not the
different Gemini Developer API 1-hour/30-minute limits.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.providers.gemini_primary_audio import (
    PrimaryTranscriptError,
    _atomic_json,
    _reserve_paid_window,
    _load_completed_window,
    _paid_window_slot_digest,
    _finish_reason,
    _usage,
    validate_staged_windows,
)

MODEL = "gemini-3.5-transcribe"
VERSION = "golden-v2-third-vertex-verbatim-v1"
MAX_WINDOW_SECONDS = 900


def _configuration(*, vocab: list[str], words: bool, diarization: bool) -> dict[str, Any]:
    # Vertex's official synchronous example permits diarization + vocabulary.
    # Word timestamps + vocabulary are conservatively kept separate pending
    # explicit endpoint-capability testing.
    if words and vocab:
        raise PrimaryTranscriptError("word timestamps and custom vocabulary require separate passes")
    if len(vocab) > 1000 or any(not isinstance(x, str) or not x.strip() for x in vocab):
        raise PrimaryTranscriptError("invalid custom vocabulary")
    options: dict[str, Any] = {"mode": "VERBATIM"}
    if words:
        options["word_timestamp"] = True
    if diarization:
        options["diarization"] = True
    if vocab:
        options["custom_vocabulary"] = vocab
    return options


def run_third_window(
    *, client: Any, staged_window: dict[str, Any], source_sha256: str,
    job_dir: Path, approval_token: str, enabled: bool,
    custom_vocabulary: list[str] | None = None,
    word_timestamp: bool = False, diarization: bool = False,
    part_factory: Any = None, config_factory: Any = None,
    approval_validator: Any = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "SKIPPED", "model": MODEL, "paid_calls": 0}
    if not approval_token.strip() or len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
        raise PrimaryTranscriptError("source identity or approval missing")
    if approval_validator is None or not approval_validator(
        approval_token, source_sha256, MODEL, staged_window
    ):
        raise PrimaryTranscriptError("no verified cost approval")
    start, end = float(staged_window["start_seconds"]), float(staged_window["end_seconds"])
    validate_staged_windows(
        [{**staged_window, "index": 0, "start_seconds": 0, "end_seconds": end-start}],
        end-start, max_seconds=MAX_WINDOW_SECONDS,
    )
    config_options = _configuration(
        vocab=custom_vocabulary or [], words=word_timestamp, diarization=diarization,
    )
    identity = {
        "model": MODEL, "version": VERSION, "source_sha256": source_sha256,
        "window": {k: staged_window[k] for k in ("index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")},
        "config": config_options,
    }
    digest = _paid_window_slot_digest(identity)
    record_path = job_dir / "gemini-third" / f"{digest}.json"
    if record_path.exists():
        return _load_completed_window(record_path, identity)

    if part_factory is None or config_factory is None:
        from google.genai import types
        if part_factory is None:
            part_factory = lambda uri, mime: types.Part.from_uri(file_uri=uri, mime_type=mime)
        if config_factory is None:
            config_factory = lambda options: types.GenerateContentConfig(
                audio_transcription_config=types.AudioTranscriptionConfig(**options)
            )

    # Reconcile unknown outcomes; never blindly repeat a potentially charged call.
    if not _reserve_paid_window(record_path, identity):
        return _load_completed_window(record_path, identity)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[part_factory(staged_window["gcs_uri"], staged_window["mime_type"])],
            config=config_factory(config_options),
        )
    except Exception as exc:
        _atomic_json(record_path, {
            "identity": identity, "status": "OUTCOME_UNKNOWN", "error_type": type(exc).__name__
        })
        raise PrimaryTranscriptError("third-model outcome unknown; reconcile before retry") from exc

    parts = list(getattr(response, "parts", None) or [])
    text = "".join(
        (str(getattr(part, "text", "") or "")
         or str(getattr(getattr(part, "audio_transcription", None), "text", "") or ""))
        for part in parts
    ).strip() or str(getattr(response, "text", "") or "").strip()
    finish = _finish_reason(response)
    valid = bool(text) and finish in {"STOP", "FINISH_REASON_STOP"}
    record = {
        "identity": identity, "status": "COMPLETE" if valid else "REQUIRES_REVIEW",
        "text": text, "finish_reason": finish, "usage_metadata": _usage(response),
        "raw_response": response.model_dump(mode="json", exclude_none=True) if hasattr(response, "model_dump") else {"text": text, "finish_reason": finish},
        "timing_authority": "CHIRP_3_ONLY",
        "third_model_timestamp_used_as_reference_only": word_timestamp,
    }
    _atomic_json(record_path, record)
    if not valid:
        raise PrimaryTranscriptError("empty or truncated third-model transcription preserved",
                                     known_record=record)
    return record
