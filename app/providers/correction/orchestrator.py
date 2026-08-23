"""End-to-end correction orchestration (owner review items 3/4/10).

Bridges the persisted per-job provider selection to the shared provider
abstraction. Official Batch processing keeps its durable submit/poll/ingest
lifecycle. Realtime MiniMax M3 correction is intentionally handled here rather
than by a second provider-specific correction runtime.

MiniMax realtime policy:
- split long transcripts into bounded windows;
- validate every window against the exact requested segment IDs;
- retry only bounded transient/format failures;
- fall back only the failed window to immutable Chirp text;
- continue trying M3 on later windows after content/request-specific failures;
- open a provider-level circuit only after consecutive transport/service
  failures, then preserve Chirp text for the remaining windows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .base import (
    PROMPT_VERSION,
    ExecutionMode,
    ProviderError,
    build_user_prompt,
    validate_correction_payload,
)
from .batch_state import AICorrectionRunStore, request_hash


MINIMAX_REALTIME_MAX_SEGMENTS = 24
MINIMAX_REALTIME_MAX_CHARS = 8_000
MINIMAX_WINDOW_MAX_ATTEMPTS = 2
MINIMAX_CIRCUIT_FAILURES = 3

_WINDOW_RETRYABLE_KINDS = {
    "rate_limit", "unreachable", "timeout", "unknown", "invalid_response",
}
_CIRCUIT_FAILURE_KINDS = {"rate_limit", "unreachable", "timeout", "unknown"}
_FATAL_PROVIDER_KINDS = {"auth", "quota"}


def build_windows(segments: list[dict[str, Any]], *,
                  max_segments_per_window: int = 40) -> list[dict[str, Any]]:
    """Deterministic batch windows; window ids derive from content."""
    windows = []
    for start in range(0, len(segments), max_segments_per_window):
        chunk = segments[start:start + max_segments_per_window]
        if not chunk:
            continue
        first, last = chunk[0]["segment_id"], chunk[-1]["segment_id"]
        wid = f"{PROMPT_VERSION}:{first}..{last}"
        windows.append({"window_id": wid, "segments": chunk})
    return windows


def build_realtime_windows(
    segments: list[dict[str, Any]], *,
    max_segments: int = MINIMAX_REALTIME_MAX_SEGMENTS,
    max_chars: int = MINIMAX_REALTIME_MAX_CHARS,
) -> list[dict[str, Any]]:
    """Build bounded realtime windows using segment count + text-size proxy."""
    max_segments = max(1, int(max_segments))
    max_chars = max(256, int(max_chars))
    windows: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        first = str(current[0]["segment_id"])
        last = str(current[-1]["segment_id"])
        windows.append({
            "window_id": f"{PROMPT_VERSION}:rt:{first}..{last}",
            "segments": current,
            "char_count": current_chars,
        })
        current = []
        current_chars = 0

    for segment in segments:
        text = str(segment.get("text", ""))
        segment_chars = len(text)
        would_exceed_count = len(current) >= max_segments
        would_exceed_chars = bool(current) and current_chars + segment_chars > max_chars
        if would_exceed_count or would_exceed_chars:
            flush()
        current.append(segment)
        current_chars += segment_chars
    flush()
    return windows


@dataclass(frozen=True)
class JobCorrectionSpec:
    """Per-job immutable provider selection (persisted at creation)."""
    job_id: str
    provider: str
    provider_profile_id: str
    model: str
    execution_mode: str
    fallback_policy: str
    source_revision: str = ""
    source_sha256: str = ""

    @property
    def is_legacy(self) -> bool:
        return not self.provider


class CorrectionOrchestrator:
    def __init__(self, *, run_store: AICorrectionRunStore | None,
                 client_factory: Callable[[str, str], Any],
                 now: Callable[[], str] | None = None):
        self.runs = run_store
        self.client_factory = client_factory
        self._now = now or (lambda: "")

    @staticmethod
    def _raw_window_corrections(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "segment_id": str(segment["segment_id"]),
                "corrected_text": str(segment.get("text", "")),
                "uncertain_terms": [],
            }
            for segment in segments
        ]

    @staticmethod
    def _correct_one_window(client: Any, segments: list[dict[str, Any]],
                            glossary: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = build_user_prompt(segments, glossary)
        expected_ids = [str(s["segment_id"]) for s in segments]
        raw = client.realtime_generate(prompt)
        parsed = _parse_json_loose(raw) if isinstance(raw, str) else raw
        corrections = validate_correction_payload(parsed, expected_ids)
        meta = getattr(client, "last_response_meta", {})
        return {
            "corrections": [c.__dict__ for c in corrections],
            "raw_response": raw,
            "provider_meta": meta if isinstance(meta, dict) else {},
        }

    def _correct_minimax_realtime(self, spec: JobCorrectionSpec,
                                  segments: list[dict[str, Any]],
                                  glossary: list[dict[str, Any]]) -> dict[str, Any]:
        client = self.client_factory(spec.provider, spec.provider_profile_id)

        # Preserve the pre-PR65 contract for generic/custom MiniMax-like
        # clients. Only the hardened provider (or an explicit test double)
        # advertises that it supports the bounded window/fallback semantics.
        if not bool(getattr(client, "supports_window_fallback", False)):
            result = self._correct_one_window(client, segments, glossary)
            return {
                "corrections": result["corrections"],
                "prompt_version": PROMPT_VERSION,
                "fallback_segment_ids": [],
                "window_results": [],
                "provider_circuit_opened": False,
                "raw_response": result["raw_response"],
            }

        windows = build_realtime_windows(segments)
        all_corrections: list[dict[str, Any]] = []
        fallback_segment_ids: list[str] = []
        window_results: list[dict[str, Any]] = []
        raw_responses: dict[str, Any] = {}
        consecutive_transport_failures = 0
        circuit_open = False

        for index, window in enumerate(windows):
            window_id = str(window["window_id"])
            window_segments = list(window["segments"])
            segment_ids = [str(s["segment_id"]) for s in window_segments]

            if circuit_open:
                all_corrections.extend(self._raw_window_corrections(window_segments))
                fallback_segment_ids.extend(segment_ids)
                window_results.append({
                    "window_id": window_id,
                    "index": index,
                    "status": "fallback_raw_chirp",
                    "reason": "provider_circuit_open",
                    "attempts": 0,
                    "segment_count": len(window_segments),
                    "char_count": int(window.get("char_count") or 0),
                })
                continue

            last_error: ProviderError | None = None
            attempts = 0
            for attempts in range(1, MINIMAX_WINDOW_MAX_ATTEMPTS + 1):
                try:
                    result = self._correct_one_window(client, window_segments, glossary)
                    all_corrections.extend(result["corrections"])
                    raw_responses[window_id] = result["raw_response"]
                    window_results.append({
                        "window_id": window_id,
                        "index": index,
                        "status": "completed",
                        "attempts": attempts,
                        "segment_count": len(window_segments),
                        "char_count": int(window.get("char_count") or 0),
                        "finish_reason": result["provider_meta"].get("finish_reason"),
                        "usage_present": isinstance(result["provider_meta"].get("usage"), dict),
                    })
                    consecutive_transport_failures = 0
                    last_error = None
                    break
                except ProviderError as exc:
                    last_error = exc
                    if exc.kind in _FATAL_PROVIDER_KINDS:
                        raise
                    if exc.kind in _WINDOW_RETRYABLE_KINDS and attempts < MINIMAX_WINDOW_MAX_ATTEMPTS:
                        continue
                    break

            if last_error is None:
                continue
            if spec.fallback_policy != "RAW_CHIRP_FALLBACK":
                raise last_error

            all_corrections.extend(self._raw_window_corrections(window_segments))
            fallback_segment_ids.extend(segment_ids)
            window_results.append({
                "window_id": window_id,
                "index": index,
                "status": "fallback_raw_chirp",
                "reason": last_error.kind,
                "safe_error": last_error.safe_message,
                "attempts": attempts,
                "segment_count": len(window_segments),
                "char_count": int(window.get("char_count") or 0),
            })

            if last_error.kind in _CIRCUIT_FAILURE_KINDS:
                consecutive_transport_failures += 1
                if consecutive_transport_failures >= MINIMAX_CIRCUIT_FAILURES:
                    circuit_open = True
            else:
                consecutive_transport_failures = 0

        expected_ids = [str(s["segment_id"]) for s in segments]
        validated = validate_correction_payload(all_corrections, expected_ids)
        return {
            "corrections": [c.__dict__ for c in validated],
            "prompt_version": PROMPT_VERSION,
            "fallback_segment_ids": fallback_segment_ids,
            "window_results": window_results,
            "provider_circuit_opened": circuit_open,
            "raw_response": {
                "window_responses": raw_responses,
                "window_results": window_results,
                "provider_circuit_opened": circuit_open,
            },
        }

    def correct_realtime(self, spec: JobCorrectionSpec,
                         segments: list[dict[str, Any]],
                         glossary: list[dict[str, Any]]) -> dict[str, Any]:
        if spec.is_legacy:
            raise ProviderError("unknown", "legacy 任務必須走原本 pipeline")
        if spec.provider == "minimax":
            return self._correct_minimax_realtime(spec, segments, glossary)

        client = self.client_factory(spec.provider, spec.provider_profile_id)
        prompt = build_user_prompt(segments, glossary)
        raw = client.realtime_generate(prompt)
        parsed = _parse_json_loose(raw) if isinstance(raw, str) else raw
        corrections = validate_correction_payload(
            parsed, [s["segment_id"] for s in segments])
        return {
            "corrections": [c.__dict__ for c in corrections],
            "prompt_version": PROMPT_VERSION,
            "raw_response": raw,
            "fallback_segment_ids": [],
            "window_results": [],
        }

    def submit_batch(self, spec: JobCorrectionSpec,
                     segments: list[dict[str, Any]],
                     glossary: list[dict[str, Any]]) -> dict[str, Any]:
        """Idempotent submission. Returns existing run if already submitted."""
        if spec.is_legacy:
            raise ProviderError("unknown", "legacy 任務必須走原本 pipeline")
        if self.runs is None:
            raise ProviderError("batch_failed", "AI Batch durable store 未設定")
        windows = build_windows(segments)
        rh = request_hash({
            "windows": [w["window_id"] for w in windows],
            "glossary_hash": request_hash(glossary),
            "model": spec.model,
            "mode": spec.execution_mode,
            "prompt_version": PROMPT_VERSION,
        })
        existing = self.runs.get_existing(
            job_id=spec.job_id,
            source_revision=spec.source_revision,
            request_sha256=rh,
        )
        if existing is not None:
            if existing.get("provider_job_id") and existing.get("status") in {
                "submitted", "processing", "completed"
            }:
                return {
                    "run_id": int(existing["id"]),
                    "provider_job_id": existing["provider_job_id"],
                    "resubmitted": False,
                    "status": existing["status"],
                }
            raise ProviderError(
                "batch_failed",
                "AI Batch submission 狀態未明，已阻止自動重送；請先人工核對 provider",
            )

        client = self.client_factory(spec.provider, spec.provider_profile_id)
        gate = getattr(client, "model_supports_batch", None)
        if callable(gate):
            ok, reason = gate(spec.model)
            if not ok:
                raise ProviderError("batch_failed", f"BATCH 未啟用：{reason}")

        claim = self.runs.claim_submission(
            job_id=spec.job_id,
            source_revision=spec.source_revision,
            source_sha256=spec.source_sha256,
            provider=spec.provider,
            provider_profile_id=spec.provider_profile_id,
            model=spec.model,
            execution_mode=spec.execution_mode,
            request_sha256=rh,
        )
        if not claim["claimed"]:
            existing = claim["run"]
            if existing.get("provider_job_id") and existing.get("status") in {
                "submitted", "processing", "completed"
            }:
                return {
                    "run_id": int(existing["id"]),
                    "provider_job_id": existing["provider_job_id"],
                    "resubmitted": False,
                    "status": existing["status"],
                    "job_status": "waiting_ai_batch",
                }
            raise ProviderError(
                "batch_failed",
                "AI Batch submission 狀態未明，已阻止自動重送；請先人工核對 provider",
            )

        try:
            provider_job_id = client.submit_batch(windows, glossary)
        except TypeError as exc:
            raise ProviderError(
                "batch_failed",
                "此 provider 的官方 Batch 介面尚未接到目前的 worker contract",
            ) from exc
        run_id = int(claim["run_id"])
        self.runs.finalize_submission(run_id, provider_job_id)
        return {
            "run_id": run_id,
            "provider_job_id": provider_job_id,
            "resubmitted": True,
            "status": "submitted",
            "job_status": "waiting_ai_batch",
        }

    def poll_pending(self, *, providers: list[str] | None = None,
                     finalize: bool = True) -> list[dict[str, Any]]:
        """Recovery scheduler tick. Restart-safe: reads durable runs table."""
        if self.runs is None:
            raise ProviderError("batch_failed", "AI Batch durable store 未設定")
        outcomes = []
        for run in self.runs.pending_batches(providers):
            try:
                client = self.client_factory(run["provider"], run["provider_profile_id"])
                state = client.get_batch(run["provider_job_id"])
            except ProviderError as exc:
                outcomes.append({
                    "run_id": run["id"],
                    "status": "error",
                    "error": exc.safe_message,
                })
                continue
            status = state["status"]
            if status == "completed":
                if finalize:
                    self.runs.update_status(run["id"], status="completed")
                outcomes.append({
                    "run_id": run["id"],
                    "status": "completed",
                    "body": state.get("body"),
                })
            elif status in ("failed", "cancelled", "expired"):
                self.runs.update_status(
                    run["id"],
                    status=status,
                    error_kind=status,
                    error_safe_message=f"provider 回報 {status}",
                )
                outcomes.append({"run_id": run["id"], "status": status})
            else:
                outcomes.append({"run_id": run["id"], "status": "processing"})
        return outcomes

    def ingest_completed(self, run_id: int, batch_body: dict[str, Any],
                         segments_by_window: dict[str, list[dict[str, Any]]]
                         ) -> dict[str, Any]:
        """Persist raw result locally + strict-validate before pipeline resume."""
        if self.runs is None:
            raise ProviderError("batch_failed", "AI Batch durable store 未設定")
        run = self.runs.get(run_id)
        if run is None:
            raise ProviderError("unknown", f"找不到 run {run_id}")
        client = self.client_factory(run["provider"], run["provider_profile_id"])
        raw_results = client.fetch_results(batch_body)
        all_corrections: list[dict[str, Any]] = []
        for item in raw_results:
            custom_id = item.get("custom_id", "")
            segments = segments_by_window.get(custom_id)
            if segments is None:
                raise ProviderError("invalid_response", f"未知 window custom_id: {custom_id}")
            body = ((item.get("response") or {}).get("body") or {})
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            if not isinstance(content, str):
                raise ProviderError("invalid_response", f"window {custom_id} 缺少回應內容")
            parsed = _parse_json_loose(content)
            expected_ids = [s["segment_id"] for s in segments]
            corrections = validate_correction_payload(parsed, expected_ids)
            all_corrections.extend(c.__dict__ for c in corrections)
        return {
            "run_id": run_id,
            "corrections": all_corrections,
            "prompt_version": PROMPT_VERSION,
        }


def _parse_json_loose(text: str) -> Any:
    """Strict JSON parse (no regex guessing). Tolerates code fences only."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("invalid_response", "模型輸出不是有效 JSON — 拒絕寫入") from exc
