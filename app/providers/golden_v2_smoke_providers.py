"""Real Google provider adapters for one approved Golden V2 240s smoke clip.

No clients/credentials are constructed at import time. Callers must first
create a private GCS object bound by SHA256, then use the isolated
SingleClipSmokeLedger.execute_one barrier and independently verified approval.
These adapters NEVER retry paid calls. Unverifiable usage blocks later stages.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import math
from typing import Any, Callable
from urllib.parse import urlsplit

from app.providers.gemini_primary_audio import INSTRUCTION

MODELS = ("gemini-3.8-flash", "chirp_3", "gemini-3.5-transcribe")


class SmokeProviderError(ValueError):
    """Bad provider response, staged source or monetary evidence."""


def require_exact_smoke_uri(uri: str, *, bucket: str, clip_sha256: str) -> str:
    """Prevent an attacker/bug pointing a paid model at unrelated Cloud files."""
    if (not isinstance(clip_sha256, str) or len(clip_sha256) != 64
            or any(c not in "0123456789abcdef" for c in clip_sha256)):
        raise SmokeProviderError("invalid derived audio identity")
    if not isinstance(bucket, str) or not bucket or "/" in bucket:
        raise SmokeProviderError("invalid private bucket")
    parsed = urlsplit(uri)
    expected_key = f"golden-v2-smoke/{clip_sha256}/clip.flac"
    if (parsed.scheme != "gs" or parsed.netloc != bucket
            or parsed.path != "/" + expected_key or parsed.query or parsed.fragment):
        raise SmokeProviderError("GCS URI does not match the approved derived sample")
    return expected_key


def _to_mapping(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    raise SmokeProviderError("provider did not return a serializable response")


def _text_from_model(response: Any, *, third: bool) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    if third:
        parts = getattr(response, "parts", None) or []
        if parts:
            text = "".join(
                str(getattr(p, "text", "") or "")
                or str(getattr(getattr(p, "audio_transcription", None),
                               "text", "") or "")
                for p in parts
            ).strip() or text
    return text


def gemini_smoke_call(
    *, client: Any, model: str, gcs_uri: str, bucket: str,
    clip_sha256: str, part_factory: Callable[..., Any] | None = None,
    config_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Exactly one Google GenAI generate_content invocation, never retries."""
    if model not in (MODELS[0], MODELS[2]):
        raise SmokeProviderError("model is not an authorized Gemini smoke recognizer")
    require_exact_smoke_uri(gcs_uri, bucket=bucket, clip_sha256=clip_sha256)
    if part_factory is None or config_factory is None:
        from google.genai import types
        if model == MODELS[2] and (
            "audio_transcription_config" not in types.GenerateContentConfig.model_fields
            or not {"mode", "word_timestamp"}.issubset(
                types.AudioTranscriptionConfig.model_fields)
        ):
            # The V1 production image pins google-genai<2; with 1.75.0,
            # AudioTranscriptionConfig does NOT accept mode/word_timestamp
            # and GenerateContentConfig lacks audio_transcription_config.
            # Refuse BEFORE any external paid request or budget claim.
            raise SmokeProviderError(
                "Gemini 3.5 requires isolated google-genai >=2.29 schema"
            )
        if part_factory is None:
            part_factory = lambda url: types.Part.from_uri(
                file_uri=url, mime_type="audio/flac")
        if config_factory is None:
            config_factory = (
                lambda selected: types.GenerateContentConfig(
                    max_output_tokens=8192,
                    thinking_config=types.ThinkingConfig(thinking_level="LOW")
                ) if selected == MODELS[0] else
                types.GenerateContentConfig(
                    audio_transcription_config=types.AudioTranscriptionConfig(
                        mode="VERBATIM", word_timestamp=False)
                )
            )
    prompt = INSTRUCTION if model == MODELS[0] else None
    contents = ([prompt] if prompt else []) + [part_factory(gcs_uri)]
    response = client.models.generate_content(
        model=model, contents=contents,
        config=config_factory(model),
    )
    text = _text_from_model(response, third=model == MODELS[2])
    raw = _to_mapping(response)
    usage = raw.get("usage_metadata")
    if not text or not isinstance(usage, dict):
        raise SmokeProviderError("missing transcription text or auditable token usage")
    candidates = raw.get("candidates") or []
    if not candidates:
        raise SmokeProviderError("missing completed candidate")
    finish = candidates[0].get("finish_reason")
    if finish not in ("STOP", "FINISH_REASON_STOP"):
        raise SmokeProviderError("model did not finish cleanly")
    return {
        "schema": "golden-v2-single-clip-gemini-response-v1",
        "model": model, "clip_sha256": clip_sha256,
        "text": text, "usage_metadata": usage,
        "finish_reason": finish, "raw_response": raw,
    }


def _duration_seconds(duration: Any) -> float | None:
    if hasattr(duration, "total_seconds"):
        return float(duration.total_seconds())
    if hasattr(duration, "seconds") and hasattr(duration, "nanos"):
        return float(duration.seconds) + float(duration.nanos) / 1e9
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return float(duration)
    return None


def chirp_smoke_call(
    *, speech_client: Any, gcs_uri: str, bucket: str, clip_sha256: str,
    project: str, request_factory: Callable[[str, str], Any] | None = None,
    result_timeout: int = 900,
) -> dict[str, Any]:
    """One Speech V2 batch_recognize on 4 minutes, with no automatic retry.

    Blocking on this one short file is intentional; a timeout leaves the
    isolated Ledger in OUTCOME_UNKNOWN, not a second charged submission.
    The operation ID may still need operator-side retrieval/reconciliation.
    """
    require_exact_smoke_uri(gcs_uri, bucket=bucket, clip_sha256=clip_sha256)
    if (not isinstance(project, str) or not project
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                   for c in project)):
        raise SmokeProviderError("invalid Speech project")
    if type(result_timeout) is not int or not 60 <= result_timeout <= 1800:
        raise SmokeProviderError("unbounded Speech result wait")
    if request_factory is None:
        from google.cloud.speech_v2.types import cloud_speech
        def request_factory(uri: str, project_id: str):
            return cloud_speech.BatchRecognizeRequest(
                recognizer=f"projects/{project_id}/locations/us/recognizers/_",
                config=cloud_speech.RecognitionConfig(
                    auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
                    language_codes=["cmn-Hant-TW"],
                    model="chirp_3",
                    features=cloud_speech.RecognitionFeatures(
                        enable_word_time_offsets=True,
                    ),
                ),
                files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    inline_response_config=cloud_speech.InlineOutputConfig(),
                ),
                processing_strategy=(
                    cloud_speech.BatchRecognizeRequest.ProcessingStrategy.
                    PROCESSING_STRATEGY_UNSPECIFIED
                ),
            )
    operation = speech_client.batch_recognize(
        request=request_factory(gcs_uri, project), retry=None, timeout=60,
    )
    op_name = getattr(getattr(operation, "operation", None), "name", None)
    if (not isinstance(op_name, str) or not op_name.startswith(
            f"projects/{project}/locations/us/operations/")):
        raise SmokeProviderError("Speech operation identity was not returned")
    result = operation.result(timeout=result_timeout)
    billed = _duration_seconds(getattr(result, "total_billed_duration", None))
    if billed is None or not math.isfinite(billed) or not 0 < billed <= 300:
        raise SmokeProviderError("unverifiable 4-minute billed Speech duration")
    results = getattr(result, "results", None)
    item = results.get(gcs_uri) if hasattr(results, "get") else (
        results[gcs_uri] if results is not None and gcs_uri in results else None
    )
    if item is None:
        raise SmokeProviderError("Speech transcript URI does not match staged clip")
    error = getattr(item, "error", None)
    if int(getattr(error, "code", 0) or 0):
        raise SmokeProviderError("Speech returned a per-file recognition error")
    transcript = getattr(item, "transcript", None)
    utterances, words = [], []
    for part in (getattr(transcript, "results", None) or []):
        alts = getattr(part, "alternatives", None) or []
        if not alts:
            continue
        alt = alts[0]
        utterances.append(str(getattr(alt, "transcript", "") or ""))
        for word in (getattr(alt, "words", None) or []):
            start = _duration_seconds(getattr(word, "start_offset", None))
            end = _duration_seconds(getattr(word, "end_offset", None))
            if (start is None or end is None or not 0 <= start < end <= 240.5):
                raise SmokeProviderError("word timing cannot be verified")
            words.append({"word": str(getattr(word, "word", "") or ""),
                          "start_seconds": start, "end_seconds": end})
    text = "".join(utterances).strip()
    if not text:
        raise SmokeProviderError("Speech returned no recognized text")
    return {
        "schema": "golden-v2-single-clip-chirp-response-v1",
        "model": "chirp_3", "clip_sha256": clip_sha256,
        "operation_name": op_name, "transcript": text,
        "words": words, "billed_duration_seconds": billed,
        "language": "cmn-Hant-TW", "timing_authority": "CHIRP_3",
    }


def smoke_cost_cents(record: dict[str, Any]) -> int:
    """Conservative standard tariff with per-response USD-cent ceiling.

    Returns accounting *estimate* from provider-reported usage, not a cloud
    invoice or provider-side guaranteed limit. No discounts are assumed.
    Rejects ambiguous counters rather than silently declaring zero spend.
    """
    if not isinstance(record, dict) or record.get("model") not in MODELS:
        raise SmokeProviderError("unknown model cost record")
    model = record["model"]
    if model == "chirp_3":
        seconds = record.get("billed_duration_seconds")
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or not 0 < seconds <= 300):
            raise SmokeProviderError("missing known billed Speech duration")
        # Speech V2 Standard $0.016/minute, no dynamic batch discount assumed.
        amount = Decimal(str(seconds)) * Decimal("0.016") / 60
    else:
        usage = record.get("usage_metadata")
        if not isinstance(usage, dict):
            raise SmokeProviderError("Gemini usage metadata missing")
        input_count = usage.get("prompt_token_count")
        output_count = usage.get("candidates_token_count")
        thinking_count = usage.get("thoughts_token_count", 0)
        if (any(type(n) is not int or n < 0 for n in (
                input_count, output_count, thinking_count))
                or input_count == 0 or output_count + thinking_count == 0):
            raise SmokeProviderError("incomplete Gemini billable token counters")
        # Public global standard inference tariffs verified 2026-10-13:
        # Gemini 3.8 Flash through 2026-12-31: $0.75/$3.75 per M tokens.
        # Gemini 3.5 Transcribe audio/text: $2/$12 per M tokens.
        input_rate, output_rate = (
            ("0.75", "3.75") if model == MODELS[0] else ("2", "12")
        )
        amount = (
            Decimal(input_count) * Decimal(input_rate)
            + Decimal(output_count + thinking_count) * Decimal(output_rate)
        ) / Decimal(1000000)
    return int((amount * 100).to_integral_value(rounding=ROUND_CEILING))
