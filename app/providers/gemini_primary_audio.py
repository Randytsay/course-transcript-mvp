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


def _fsync_audit_directory(directory: Path) -> None:
    """Durably persist audit namespace changes before any external paid call."""
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        _fsync_audit_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _paid_window_slot_digest(identity: dict[str, Any]) -> str:
    """Lock one physical audio/model slot, independent of mutable prompt/config.

    Any changed transcript hints, prompt version or third-model settings must
    not buy a second recognition of the same audio window under the old claim.
    The existing identity is still compared in full before cached reuse.
    """
    slot = {key: identity[key] for key in ("model", "source_sha256", "window")}
    return hashlib.sha256(
        json.dumps(slot, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _reserve_paid_window(path: Path, identity: dict[str, Any]) -> bool:
    """Create the durable paid-request barrier using an exclusive filesystem op.

    The reservation is never unlinked on failure: even a torn/empty barrier
    means an external request *might* have been sent and needs reconciliation.
    O_EXCL prevents two independent workers from passing an exists() race.
    Requires a shared, POSIX-like filesystem with exclusive-create semantics.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # The audit directory might itself be newly created. Persist its entry.
    _fsync_audit_directory(path.parent.parent)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"identity": identity, "status": "REQUEST_STARTED",
                 "human_review_required": True},
                handle, ensure_ascii=False, indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        # Also sync an incomplete reservation. If fsync fails, the caller
        # must NOT proceed to a paid provider call.
        _fsync_audit_directory(path.parent)
    return True


def _reject_legacy_paid_slot(path: Path, identity: dict[str, Any]) -> None:
    """Fail closed if older full-prompt-keyed receipts may represent this slot.

    A pre-existing V2 job may use a different on-disk receipt filename.
    Looking only for the new slot filename could otherwise cause duplicate
    billing after a code upgrade. A human must reconcile and safely reindex
    old immutable results; this function never modifies them.
    """
    for previous in path.parent.glob("*.json"):
        if previous.name == path.name:
            continue
        if previous.is_symlink():
            raise PrimaryTranscriptError(
                "symlinked paid audit requires operator reconciliation"
            )
        try:
            receipt = json.loads(previous.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PrimaryTranscriptError(
                "unreadable legacy paid audit; block further provider submissions"
            ) from exc
        if not isinstance(receipt, dict) or not isinstance(receipt.get("identity"), dict):
            raise PrimaryTranscriptError(
                "unbound legacy paid audit; operator reconciliation required"
            )
        old = receipt["identity"]
        if all(old.get(key) == identity.get(key) for key in
               ("model", "source_sha256", "window")):
            raise PrimaryTranscriptError(
                "prior paid audio window uses a legacy receipt; reconcile before resubmission"
            )


def _load_completed_window(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    """Only reuse a validated COMPLETE record; all other states fail closed."""
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrimaryTranscriptError(
            "unreadable previous paid-request barrier; operator reconciliation required"
        ) from exc
    if (not isinstance(cached, dict) or cached.get("identity") != identity
            or cached.get("status") != "COMPLETE"):
        raise PrimaryTranscriptError("previous attempt requires operator reconciliation")
    if (cached.get("finish_reason") not in {"STOP", "FINISH_REASON_STOP"}
            or not isinstance(cached.get("text"), str) or not cached["text"].strip()):
        # A COMPLETE stamp cannot override an actual truncated/held reply.
        # Preserve the known response and its usage; never issue a replacement call.
        raise PrimaryTranscriptError("cached primary is blocked or truncated; reconcile before repair",
                                     known_record=cached)
    return cached


def _load_completed_primary_window(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    """Primary-specific cache reuse: a COMPLETE stamp must not hide a held reply.

    The primary adapter records ``human_review_required`` explicitly, so a
    cached COMPLETE record that still claims review is required is a legacy
    contradiction and must not be replayed as a finished window.
    """
    cached = _load_completed_window(path, identity)
    if cached.get("human_review_required") is not False:
        raise PrimaryTranscriptError("cached primary still requires review; reconcile before repair",
                                     known_record=cached)
    return cached


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
    max_output_tokens: int | None = None, thinking_level: str | None = None,
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
    output_limit = 16384 if max_output_tokens is None else max_output_tokens
    effort = "LOW" if thinking_level is None else thinking_level
    if type(output_limit) is not int or not 1 <= output_limit <= 16384 or effort != "LOW":
        raise PrimaryTranscriptError("bounded output tokens and LOW thinking required")
    generation_config = {"temperature": 0, "max_output_tokens": output_limit,
                         "thinking_config": {"thinking_level": effort}}
    identity = {
        "model": MODEL, "prompt_version": PROMPT_VERSION,
        "generation_config": generation_config,
        "source_sha256": source_sha256, "window": {k: staged_window[k] for k in ("index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")},
        "context_sha256": hashlib.sha256(reference_terms.encode("utf-8")).hexdigest(),
    }
    digest = _paid_window_slot_digest(identity)
    audit_path = job_dir / "gemini-primary" / f"{digest}.json"
    if audit_path.exists():
        return _load_completed_primary_window(audit_path, identity)
    _reject_legacy_paid_slot(audit_path, identity)
    if part_factory is None:
        from google.genai import types
        part_factory = lambda uri, mime: types.Part.from_uri(file_uri=uri, mime_type=mime)
        config = types.GenerateContentConfig(**generation_config)
    else:
        config = generation_config
    # Persist a durable barrier BEFORE the external call. On timeout, the
    # provider may have charged/finished even if its response was lost. Never
    # automatically submit a second call with the same identity.
    if not _reserve_paid_window(audit_path, identity):
        # Another process won the reservation. It may already have a
        # verified COMPLETE response, but must never trigger another POST.
        return _load_completed_primary_window(audit_path, identity)
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
        finish = record.get("finish_reason")
        if (record.get("status") != "COMPLETE"
                or (finish is not None and finish not in {"STOP", "FINISH_REASON_STOP"})
                or not str(record.get("text", "")).strip()):
            raise PrimaryTranscriptError("incomplete, blocked or truncated Gemini transcription window")
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


def assemble_reviewed_overlaps(
    windows: list[dict[str, Any]], records: list[dict[str, Any]],
    duration_seconds: float, *, seam_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply an authenticated operator's *audio-reviewed* text seam decisions.

    Character offsets refer only to the original immutable model responses;
    they are not synthetic timestamps. The exact audio overlap must be
    inspected and cited for every seam. Any missing/invalid decision blocks
    progress. This does NOT attest to whole-file acoustic completeness.
    """
    baseline = assemble_windows(windows, records, duration_seconds)
    required = {tuple(item["between_windows"]) for item in baseline["review_items"]}
    decisions: dict[tuple[int, int], dict[str, Any]] = {}
    if not required:
        raise PrimaryTranscriptError("no Gemini overlap review to resolve")
    for decision in seam_decisions:
        pair = decision.get("between_windows")
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(i) is not int for i in pair)):
            raise PrimaryTranscriptError("invalid overlap decision identity")
        key = tuple(pair)
        if key not in required or key in decisions:
            raise PrimaryTranscriptError("duplicate or unknown overlap decision")
        previous, following = windows[key[0]], windows[key[1]]
        expected_start = round(float(following["start_seconds"]) * 1000)
        expected_end = round(float(previous["end_seconds"]) * 1000)
        if (type(decision.get("reviewed_audio_start_ms")) is not int or
                type(decision.get("reviewed_audio_end_ms")) is not int or
                decision["reviewed_audio_start_ms"] != expected_start or
                decision["reviewed_audio_end_ms"] != expected_end or
                decision.get("audio_review_confirmed") is not True or
                not isinstance(decision.get("notes"), str) or
                not decision["notes"].strip()):
            raise PrimaryTranscriptError("full overlap audio review evidence required")
        left = str(records[key[0]]["text"]).strip()
        right = str(records[key[1]]["text"]).strip()
        a, b = decision.get("left_keep_chars"), decision.get("right_drop_chars")
        if (type(a) is not int or type(b) is not int or
                not 0 < a <= len(left) or not 0 <= b < len(right)):
            raise PrimaryTranscriptError("overlap text cuts out of bounds")
        decisions[key] = decision
    if set(decisions) != required:
        raise PrimaryTranscriptError("every audio overlap needs an explicit decision")
    texts = [str(record["text"]).strip() for record in records]
    contributed = []
    removed = []
    for index, text in enumerate(texts):
        start = decisions[(index - 1, index)]["right_drop_chars"] if (index - 1, index) in decisions else 0
        end = decisions[(index, index + 1)]["left_keep_chars"] if (index, index + 1) in decisions else len(text)
        if end <= start or not text[start:end].strip():
            raise PrimaryTranscriptError("overlap cuts remove a complete window contribution")
        contributed.append(text[start:end])
        removed.append({"window": index, "prefix": text[:start], "suffix": text[end:]})
    return {
        "text": "\n\n".join(contributed), "window_count": len(windows),
        "structural_coverage": "PASS", "acoustic_coverage": "SEAMS_REVIEWED_WHOLE_FILE_UNVERIFIED",
        "review_items": [], "reviewed_seams": seam_decisions,
        "discarded_window_text": removed,
        "ready_for_chirp_comparison": True, "golden_approved": False,
    }
