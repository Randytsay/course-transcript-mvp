"""Optional V2 independent Gemini 3.5 Transcribe adapter.

This is an isolated, paid-call-gated provider primitive. It does not replace
Chirp acoustic word timing or the Gemini 3.8 primary transcript. Use a client
configured for the Vertex/Enterprise Agent Platform under the same approved
Google Cloud project, and persist an external provider-attempt ledger first.
"""
from __future__ import annotations

import re
from typing import Any

from app.providers.gemini_audio_primary_v2 import AudioChunk

MODEL = "gemini-3.5-transcribe"
# Conservative floor across currently documented transcription modes. The
# backend must also verify the provider's current capability on real rollout.
CONSERVATIVE_CHUNK_LIMIT_MS = 15 * 60 * 1000
_ALLOWED_MIME = frozenset({
    "audio/wav", "audio/mp3", "audio/mpeg", "audio/flac", "audio/ogg",
    "audio/aac", "audio/m4a", "audio/webm",
})


def transcribe_independent_chunk(
    client: Any,
    *,
    gcs_uri: str,
    mime_type: str,
    source_sha256: str,
    chunk: AudioChunk,
    approval_reference: str | None = None,
    custom_vocabulary: list[str] | None = None,
    word_timestamp: bool = False,
    diarization: bool = False,
    language_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Verbatim transcription with strict argument constraints and no auto QA promotion."""
    if not approval_reference or not approval_reference.strip():
        raise PermissionError("Provider call requires a specific approved source and cost reference")
    if not re.fullmatch(r"gs://[A-Za-z0-9._-]+/.+", gcs_uri):
        raise ValueError("Expected a pre-materialized private GCS audio chunk")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", source_sha256):
        raise ValueError("Invalid source SHA256")
    if mime_type not in _ALLOWED_MIME:
        raise ValueError("Unsupported audio MIME")
    if chunk.start_ms < 0 or not (0 < chunk.duration_ms <= CONSERVATIVE_CHUNK_LIMIT_MS):
        raise ValueError("Audio chunk exceeds conservative 15-minute duration limit")
    terms = list(custom_vocabulary or [])
    if len(terms) > 1000 or any(not isinstance(term, str) or not term.strip() for term in terms):
        raise ValueError("Invalid custom vocabulary")
    if terms and (word_timestamp or diarization):
        raise ValueError("Custom vocabulary cannot accompany word timing/diarization")
    langs = list(language_codes or [])
    if len(langs) > 4 or any(not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", lang) for lang in langs):
        raise ValueError("Invalid language codes")
    from google.genai import types

    config = types.GenerateContentConfig(
        audio_transcription_config=types.AudioTranscriptionConfig(
            mode="VERBATIM",
            custom_vocabulary=terms or None,
            word_timestamp=word_timestamp or None,
            diarization=diarization or None,
            language_codes=langs or None,
        )
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)],
        config=config,
    )
    parts = getattr(response, "parts", None) or []
    text = str(getattr(response, "text", "") or "")
    if not text.strip():
        text = "".join(
            str(getattr(part, "text", "") or
                getattr(getattr(part, "audio_transcription", None), "text", "") or "")
            for part in parts
        )
    return {
        "model": MODEL,
        "source_sha256": source_sha256.lower(),
        "chunk_index": chunk.index,
        "start_ms": chunk.start_ms,
        "end_ms": chunk.end_ms,
        "text": text,
        "candidate_valid": bool(text.strip()),
        "needs_acoustic_validation": True,
        "never_authoritative_timing": True,
        "word_timestamp_requested": word_timestamp,
        "approval_reference": approval_reference,
        "usage_metadata": (
            response.usage_metadata.model_dump(mode="json")
            if getattr(response, "usage_metadata", None) is not None else None
        ),
    }
