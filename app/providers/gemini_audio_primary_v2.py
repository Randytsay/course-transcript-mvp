"""V2 Gemini 3.8 audio-first transcription primitives (not a production job runner).

No network is touched on import. Live calls require a separately checked,
non-empty approval reference; the durable worker must also reserve estimated
cost and source-specific scope before invoking this adapter.

This module emits a *candidate* transcript, never a Golden/verified transcript.
Chirp 3 must independently verify acoustic coverage and own subtitle timing.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

MODEL = "gemini-3.8-flash"
PROMPT_VERSION = "golden-v2-gemini-audio-primary-v1"
THINKING_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
MAX_REFERENCE_CHARS = 12000


@dataclass(frozen=True)
class AudioChunk:
    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def plan_audio_chunks(
    duration_ms: int,
    *,
    max_chunk_ms: int = 3600000,
    overlap_ms: int = 10000,
) -> list[AudioChunk]:
    """Prefer an entire file when it fits; otherwise split with overlap.

    Actual GCS objects must be materialized from these windows by the worker.
    A time fragment appended to a GCS URI is *not* an audio crop.
    """
    if duration_ms <= 0 or max_chunk_ms <= 0 or not (0 <= overlap_ms < max_chunk_ms):
        raise ValueError("Invalid audio duration, chunk maximum or overlap")
    chunks: list[AudioChunk] = []
    start = 0
    while start < duration_ms:
        end = min(start + max_chunk_ms, duration_ms)
        chunks.append(AudioChunk(len(chunks), start, end))
        if end == duration_ms:
            break
        start = end - overlap_ms
    return chunks


def primary_prompt(
    *,
    content_mode: str = "generic",
    reference_text: str = "",
) -> str:
    """References bias spellings only and never authorize missing speech."""
    if len(reference_text) > MAX_REFERENCE_CHARS:
        raise ValueError("Reference context exceeds approved maximum")
    if content_mode not in {"generic", "dacheng_buddhist", "market_america_training", "hvac_energy"}:
        raise ValueError("Unrecognized course content mode")
    instruction = (
        "請仔細聆聽這份原始音檔，輸出完整、忠於發音內容的繁體中文逐字稿。"
        "要保留台語或國台語混用的意思、每個實際唸出的句子與有意義的重複。"
        "請整理正確標點與合理段落，但不可摘要、刪減、加入沒有朗讀的內容、"
        "自行補足缺漏經文，也不要輸出時間碼。若某段聲音難辨識，明確標為"
        "「[聽不清楚]」，不要猜測。只輸出逐字稿正文。"
    )
    if content_mode == "dacheng_buddhist":
        instruction += "經文是拼字與字詞參考；老師的白話講解與改述不得改寫成原經文。"
    elif content_mode == "market_america_training":
        instruction += "保留商品、營養與收入相關原話，不要擅自增加療效或收入承諾。"
    elif content_mode == "hvac_energy":
        instruction += "注意原音中的工程單位、縮寫、標準編號，不可擅改數值。"
    if reference_text:
        instruction += (
            "\n\n以下僅是可能相關的參考詞句，不是要你照抄的答案：\n"
            + reference_text
        )
    return instruction


def _completion_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "UNKNOWN"
    reason = getattr(candidates[0], "finish_reason", None)
    return str(getattr(reason, "name", reason) or "UNKNOWN").upper()


def transcribe_audio_chunk(
    client: Any,
    *,
    gcs_uri: str,
    mime_type: str,
    source_sha256: str,
    chunk: AudioChunk,
    approval_reference: str | None = None,
    content_mode: str = "generic",
    reference_text: str = "",
    thinking_level: str = "MEDIUM",
) -> dict[str, Any]:
    """Generate a single auditable *unverified* transcript chunk.

    This is a low-level provider adapter. Caller must persist attempt state
    before invoking it and must never resubmit an uncertain/terminal request
    without a new explicitly authorized attempt ID.
    """
    if not approval_reference or not approval_reference.strip():
        raise PermissionError("Paid provider call needs an exact approved job/cost reference")
    if not re.fullmatch(r"gs://[A-Za-z0-9._-]+/.+", gcs_uri):
        raise ValueError("Audio must reference a private, pre-materialized GCS object")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", source_sha256):
        raise ValueError("Source SHA-256 missing or invalid")
    if mime_type not in {"audio/wav", "audio/mpeg", "audio/mp3", "audio/flac", "audio/ogg", "audio/mp4"}:
        raise ValueError("Unsupported Gemini audio MIME type")
    if chunk.start_ms < 0 or chunk.end_ms <= chunk.start_ms:
        raise ValueError("Invalid audio chunk")
    if thinking_level not in THINKING_LEVELS:
        raise ValueError("Only LOW/MEDIUM/HIGH thinking is supported")
    prompt = primary_prompt(content_mode=content_mode, reference_text=reference_text)

    # Lazy SDK import keeps local chunk-planning and review tests provider-free.
    from google.genai import types

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type),
            types.Part.from_text(text=prompt),
        ],
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            max_output_tokens=65536,
        ),
    )
    raw_text = str(getattr(response, "text", "") or "")
    reason = _completion_reason(response)
    # Empty, truncated, or blocked responses must not become FULL_TRANSCRIPT.
    has_text = bool(raw_text.strip())
    truncated = "MAX_TOKENS" in reason or "SAFETY" in reason or "RECITATION" in reason
    usage = getattr(response, "usage_metadata", None)
    return {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "source_sha256": source_sha256.lower(),
        "gcs_uri_sha256": hashlib.sha256(gcs_uri.encode("utf-8")).hexdigest(),
        "chunk_index": chunk.index,
        "start_ms": chunk.start_ms,
        "end_ms": chunk.end_ms,
        "text": raw_text,
        "finish_reason": reason,
        "usage_metadata": usage.model_dump(mode="json") if usage else None,
        "candidate_valid": has_text and not truncated,
        "needs_acoustic_validation": True,
        "approval_reference": approval_reference,
    }


def assemble_primary_candidates(
    chunks: list[dict[str, Any]],
    *,
    expected: list[AudioChunk],
) -> dict[str, Any]:
    """Conservatively combine candidates; ambiguous overlapped text requires review.

    Only exact suffix/prefix duplicates are suppressed. Differences are retained
    and flagged instead of silently throwing away spoken text.
    """
    if len(chunks) != len(expected):
        raise ValueError("Missing candidate chunks")
    combined = ""
    issues: list[dict[str, Any]] = []
    for index, (got, span) in enumerate(zip(chunks, expected)):
        if (got.get("chunk_index"), got.get("start_ms"), got.get("end_ms")) != (
            span.index, span.start_ms, span.end_ms
        ):
            raise ValueError("Candidate order/window mismatch")
        text = str(got.get("text") or "").strip()
        if not got.get("candidate_valid") or not text:
            issues.append({"chunk": index, "reason": "missing_or_truncated"})
            continue
        if index and span.start_ms < expected[index - 1].end_ms:
            max_len = min(160, len(combined), len(text))
            matching = next(
                (n for n in range(max_len, 4, -1) if combined[-n:] == text[:n]),
                0,
            )
            if matching:
                text = text[matching:].lstrip()
            else:
                issues.append({"chunk": index, "reason": "ambiguous_overlap"})
        combined += ("\n" if combined and text else "") + text
    return {
        "model": MODEL,
        "text": combined,
        "chunk_count": len(expected),
        "issues": issues,
        "primary_candidate_complete": not issues and bool(combined),
        # Even a complete response cannot independently establish audio coverage.
        "golden_eligible": False,
        "needs_chirp_and_vad": True,
    }
