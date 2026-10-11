"""Golden V2: audit-first Gemini 3.8 full-audio transcript *adapter*.

This module does not mutate Drive or submit paid work on import. An authorized
worker must stage audio in private GCS, approve cost, and call run_window().
It is deliberately separate from V1's Chirp-first segment correction.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

MODEL = "gemini-3.8-flash"
PROMPT_VERSION = "golden-v2-primary-audio-v1"
MAX_WINDOW_SECONDS = 2700
ALLOWED_MIME = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/flac", "audio/ogg", "audio/mp4", "audio/aac", "audio/webm"}

INSTRUCTION = (
    "請完整聆聽提供的錄音，依原始播放順序輸出完整繁體中文逐字稿。"
    "不摘要、不省略可辨識的口語、重複、聽眾回應或中途改述。"
    "辨認國語與台語混用，保留說話者原意。"
    "有經文時謹慎核對語音：只有真的聽到的內容才可寫入，"
    "不可用熟悉的經文補上沒有朗讀的字句。"
    "以自然標點與段落呈現，不附帶解釋，不生成時間碼。"
    "無法辨識處請明確標示〔聽不清楚〕，絕對不可猜測。"
)


class PrimaryTranscriptError(RuntimeError):
    def __init__(self, message: str, *, known_record: dict[str, Any] | None = None):
        super().__init__(message)
        self.known_record = known_record


def plan_windows(duration_seconds: float, *, max_seconds: float = MAX_WINDOW_SECONDS, overlap_seconds: float = 10.0) -> list[dict[str, float | int]]:
    """Plan source-time windows; a different, non-paid step stages exact GCS slices."""
    if not (duration_seconds > 0 and max_seconds > 0 and 0 <= overlap_seconds < max_seconds):
        raise ValueError("invalid duration/window/overlap")
    windows: list[dict[str, float | int]] = []
    start = 0.0
    while start < duration_seconds:
        end = min(duration_seconds, start + max_seconds)
        windows.append({"index": len(windows), "start_seconds": round(start, 3), "end_seconds": round(end, 3)})
        if end >= duration_seconds:
            break
        start = end - overlap_seconds
    return windows


def validate_staged_windows(windows: list[dict[str, Any]], duration_seconds: float, *, max_seconds: float = MAX_WINDOW_SECONDS) -> None:
    """No missing, out-of-order, or excessively long source windows."""
    if not windows or duration_seconds <= 0:
        raise PrimaryTranscriptError("missing audio windows")
    last_end = 0.0
    for n, window in enumerate(windows):
        if window.get("index") != n:
            raise PrimaryTranscriptError("window index/order conflict")
        start, end = float(window["start_seconds"]), float(window["end_seconds"])
        if end <= start or end - start > max_seconds + 0.005 or start < 0 or end > duration_seconds + 0.005:
            raise PrimaryTranscriptError("invalid window bounds")
        if n == 0 and start > 0.005 or n and start > last_end + 0.005:
            raise PrimaryTranscriptError("audible interval not structurally covered")
        if n and end <= last_end:
            raise PrimaryTranscriptError("window makes no progress")
        uri = str(window.get("gcs_uri", ""))
        if not uri.startswith("gs://") or uri.count("/") < 3:
            raise PrimaryTranscriptError("private GCS URI required")
        if window.get("mime_type") not in ALLOWED_MIME:
            raise PrimaryTranscriptError("unsupported audio mime")
        last_end = end
    if abs(last_end - duration_seconds) > 0.005:
        raise PrimaryTranscriptError("uncovered final audio interval")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".primary-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _usage(response: Any) -> dict[str, Any]:
    value = getattr(response, "usage_metadata", None)
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    return {"unparsed_usage_type": type(value).__name__}


def _finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return str(getattr(reason, "name", getattr(reason, "value", reason)) or "").upper()


def run_window(
    *, client: Any, staged_window: dict[str, Any], source_sha256: str,
    job_dir: Path, approval_token: str, reference_terms: str = "",
    part_factory: Any = None, approval_validator: Any = None,
) -> dict[str, Any]:
    """Run at most one paid call, only after explicit worker-side authorization.

    The caller must preflight cost + source identity and supply a non-empty
    approval_token. approval_validator must check the immutable job ledger;
    passing a token alone never permits a paid request.
    """
    if not approval_token.strip() or len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
        raise PrimaryTranscriptError("source identity or approval missing")
    # This library never accepts a self-asserted token as sufficient authority.
    # The production worker must supply a store-backed validator that checks
    # approved source checksum, selected model, exact job/cost revision.
    if approval_validator is None or not approval_validator(
        approval_token, source_sha256, MODEL, staged_window
    ):
        raise PrimaryTranscriptError("no verified cost approval")
    start = float(staged_window["start_seconds"])
    end = float(staged_window["end_seconds"])
    validate_staged_windows([{**staged_window, "index": 0, "start_seconds": 0, "end_seconds": end-start}], end-start)
    prompt = INSTRUCTION + ("\n\n專有詞彙僅供核對拼寫，不得作為額外錄音內容：\n" + reference_terms[:8000] if reference_terms else "")
    identity = {
        "model": MODEL, "prompt_version": PROMPT_VERSION,
        "source_sha256": source_sha256, "window": {k: staged_window[k] for k in ("index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")},
        "context_sha256": hashlib.sha256(reference_terms.encode("utf-8")).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    audit_path = job_dir / "gemini-primary" / f"{digest}.json"
    if audit_path.exists():
        cached = json.loads(audit_path.read_text(encoding="utf-8"))
        if cached.get("identity") != identity or cached.get("status") != "COMPLETE":
            raise PrimaryTranscriptError("previous attempt requires operator reconciliation")
        return cached
    if part_factory is None:
        from google.genai import types
        part_factory = lambda uri, mime: types.Part.from_uri(file_uri=uri, mime_type=mime)
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
        )
    else:
        config = {"thinking_config": {"thinking_level": "MEDIUM"}}
    # Persist a durable barrier BEFORE the external call. On timeout, the
    # provider may have charged/finished even if its response was lost. Never
    # automatically submit a second call with the same identity.
    _atomic_json(audit_path, {"identity": identity, "status": "REQUEST_STARTED",
                              "human_review_required": True})
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[prompt, part_factory(staged_window["gcs_uri"], staged_window["mime_type"])],
            config=config,
        )
    except Exception as exc:
        _atomic_json(audit_path, {
            "identity": identity, "status": "OUTCOME_UNKNOWN",
            "human_review_required": True,
            "error_type": type(exc).__name__,
        })
        raise PrimaryTranscriptError("provider outcome unknown; reconcile before retry") from exc
    text = str(getattr(response, "text", "") or "").strip()
    finish = _finish_reason(response)
    valid = bool(text) and finish in {"STOP", "FINISH_REASON_STOP"}
    raw = response.model_dump(mode="json", exclude_none=True) if hasattr(response, "model_dump") else {"text": text, "finish_reason": finish}
    record = {
        "identity": identity, "status": "COMPLETE" if valid else "REQUIRES_REVIEW",
        "text": text, "finish_reason": finish, "usage_metadata": _usage(response),
        "raw_response": raw, "human_review_required": not valid,
    }
    _atomic_json(audit_path, record)
    if not valid:
        raise PrimaryTranscriptError("empty, blocked or truncated model response preserved for review", known_record=record)
    return record


def assemble_windows(windows: list[dict[str, Any]], records: list[dict[str, Any]], duration_seconds: float) -> dict[str, Any]:
    """Preserve all text; overlapping windows require audio-guided adjudication.

    This is only a structural gate, not acoustic coverage evidence.
    """
    validate_staged_windows(windows, duration_seconds)
    if len(records) != len(windows):
        raise PrimaryTranscriptError("window count mismatch")
    texts = []
    review = []
    for index, (window, record) in enumerate(zip(windows, records)):
        if record.get("status") != "COMPLETE" or not str(record.get("text", "")).strip():
            raise PrimaryTranscriptError("incomplete Gemini transcription window")
        if record.get("identity", {}).get("window") != {k: window[k] for k in ("index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")}:
            raise PrimaryTranscriptError("source/window identity mismatch")
        if index and float(window["start_seconds"]) < float(windows[index-1]["end_seconds"]):
            review.append({"reason": "overlap_audio_alignment_required", "between_windows": [index-1, index]})
        texts.append(str(record["text"]).strip())
    return {
        "text": "\n\n".join(texts), "window_count": len(windows),
        "structural_coverage": "PASS", "acoustic_coverage": "UNVERIFIED",
        "review_items": review, "ready_for_chirp_comparison": not bool(review),
        "golden_approved": False,
    }
