"""End-to-end correction orchestration (owner review items 3/4/10).

Bridges the persisted per-job provider selection to the new provider
abstraction, with official-batch lifecycle: build windows -> request hash ->
durable check (never resubmit) -> submit -> persist job id -> waiting state
-> recovery poll -> ingest inline results -> strict validation.

Legacy GEMINI_FIRST / M3_FIRST jobs bypass this module entirely and keep
their original pipeline path.
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


def build_windows(segments: list[dict[str, Any]], *,
                  max_segments_per_window: int = 40) -> list[dict[str, Any]]:
    """Deterministic correction windows; window ids derive from content."""
    windows = []
    for start in range(0, len(segments), max_segments_per_window):
        chunk = segments[start:start + max_segments_per_window]
        first, last = chunk[0]["segment_id"], chunk[-1]["segment_id"]
        wid = f"{PROMPT_VERSION}:{first}..{last}"
        windows.append({"window_id": wid, "segments": chunk})
    return windows


@dataclass(frozen=True)
class JobCorrectionSpec:
    """Per-job immutable provider selection (persisted at creation)."""
    job_id: str
    provider: str
    provider_profile_id: str
    model: str
    execution_mode: str          # REALTIME / BATCH
    fallback_policy: str         # RAW_CHIRP_FALLBACK / <other-provider>
    source_revision: str = ""
    source_sha256: str = ""

    @property
    def is_legacy(self) -> bool:
        return not self.provider  # legacy jobs have empty provider


class CorrectionOrchestrator:
    def __init__(self, *, run_store: AICorrectionRunStore,
                 client_factory: Callable[[str, str], Any],
                 now: Callable[[], str] | None = None):
        self.runs = run_store
        self.client_factory = client_factory   # (provider, profile_id) -> client
        self._now = now or (lambda: "")

    # -- realtime -----------------------------------------------------------

    def correct_realtime(self, spec: JobCorrectionSpec,
                         segments: list[dict[str, Any]],
                         glossary: list[dict[str, Any]]) -> dict[str, Any]:
        if spec.is_legacy:
            raise ProviderError("unknown", "legacy 任務必須走原本 pipeline")
        client = self.client_factory(spec.provider, spec.provider_profile_id)
        prompt = build_user_prompt(segments, glossary)
        raw = client.realtime_generate(prompt)
        parsed = _parse_json_loose(raw) if isinstance(raw, str) else raw
        corrections = validate_correction_payload(
            parsed, [s["segment_id"] for s in segments])
        return {"corrections": [c.__dict__ for c in corrections],
                "prompt_version": PROMPT_VERSION,
                # Keep the provider response in the job's immutable audit
                # directory.  It is deliberately not returned by the API.
                "raw_response": raw}

    # -- batch lifecycle ------------------------------------------------------

    def submit_batch(self, spec: JobCorrectionSpec,
                     segments: list[dict[str, Any]],
                     glossary: list[dict[str, Any]]) -> dict[str, Any]:
        """Idempotent submission. Returns existing run if already submitted."""
        if spec.is_legacy:
            raise ProviderError("unknown", "legacy 任務必須走原本 pipeline")
        windows = build_windows(segments)
        rh = request_hash({
            "windows": [w["window_id"] for w in windows],
            "glossary_hash": request_hash(glossary),
            "model": spec.model, "mode": spec.execution_mode,
            "prompt_version": PROMPT_VERSION,
        })
        existing = self.runs.get_existing(
            job_id=spec.job_id, source_revision=spec.source_revision,
            request_sha256=rh)
        if existing is not None:
            # NEVER resubmit a paid batch. A provider id is missing only for
            # the crash window covered by the durable submission claim.
            if existing.get("provider_job_id") and existing.get("status") in {
                "submitted", "processing", "completed"
            }:
                return {"run_id": int(existing["id"]),
                        "provider_job_id": existing["provider_job_id"],
                        "resubmitted": False,
                        "status": existing["status"]}
            raise ProviderError(
                "batch_failed",
                "AI Batch submission 狀態未明，已阻止自動重送；請先人工核對 provider",
            )

        client = self.client_factory(spec.provider, spec.provider_profile_id)

        # OpenRouter model-specific gating — server-side, not trusted from UI
        gate = getattr(client, "model_supports_batch", None)
        if callable(gate):
            ok, reason = gate(spec.model)
            if not ok:
                raise ProviderError("batch_failed", f"BATCH 未啟用：{reason}")

        claim = self.runs.claim_submission(
            job_id=spec.job_id, source_revision=spec.source_revision,
            source_sha256=spec.source_sha256, provider=spec.provider,
            provider_profile_id=spec.provider_profile_id, model=spec.model,
            execution_mode=spec.execution_mode, request_sha256=rh,
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
            # Vertex's native BatchPrediction adapter uses a GCS input/output
            # contract and is not the same as the inline-window contract used
            # by this worker.  Never accidentally call it with the wrong
            # argument shape or silently fall back to realtime.
            raise ProviderError(
                "batch_failed",
                "此 provider 的官方 Batch 介面尚未接到目前的 worker contract",
            ) from exc
        run_id = int(claim["run_id"])
        self.runs.finalize_submission(run_id, provider_job_id)
        return {"run_id": run_id, "provider_job_id": provider_job_id,
                "resubmitted": True, "status": "submitted",
                "job_status": "waiting_ai_batch"}

    def poll_pending(self, *, providers: list[str] | None = None,
                     finalize: bool = True) -> list[dict[str, Any]]:
        """Recovery scheduler tick. Restart-safe: reads durable runs table."""
        outcomes = []
        for run in self.runs.pending_batches(providers):
            try:
                client = self.client_factory(run["provider"],
                                             run["provider_profile_id"])
                state = client.get_batch(run["provider_job_id"])
            except ProviderError as exc:
                outcomes.append({"run_id": run["id"], "status": "error",
                                 "error": exc.safe_message})
                continue
            status = state["status"]
            if status == "completed":
                # A recovery caller may need to persist the raw provider body
                # before marking the run completed.  The default keeps the
                # original library contract used by tests and admin tooling.
                if finalize:
                    self.runs.update_status(run["id"], status="completed")
                outcomes.append({"run_id": run["id"], "status": "completed",
                                 "body": state.get("body")})
            elif status in ("failed", "cancelled", "expired"):
                self.runs.update_status(run["id"], status=status,
                                        error_kind=status,
                                        error_safe_message=f"provider 回報 {status}")
                outcomes.append({"run_id": run["id"], "status": status})
            else:
                outcomes.append({"run_id": run["id"], "status": "processing"})
        return outcomes

    def ingest_completed(self, run_id: int, batch_body: dict[str, Any],
                         segments_by_window: dict[str, list[dict[str, Any]]]
                         ) -> dict[str, Any]:
        """Persist raw result locally + strict-validate before pipeline resume."""
        client_for_run = None
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
                raise ProviderError("invalid_response",
                                    f"未知 window custom_id: {custom_id}")
            body = ((item.get("response") or {}).get("body") or {})
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            if not isinstance(content, str):
                raise ProviderError("invalid_response",
                                    f"window {custom_id} 缺少回應內容")
            parsed = _parse_json_loose(content)
            expected_ids = [s["segment_id"] for s in segments]
            corrections = validate_correction_payload(parsed, expected_ids)
            all_corrections.extend(c.__dict__ for c in corrections)
        return {"run_id": run_id,
                "corrections": all_corrections,
                "prompt_version": PROMPT_VERSION}


def _parse_json_loose(text: str) -> Any:
    """Strict JSON parse (no regex guessing). Tolerates code fences only."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("invalid_response",
                            "模型輸出不是有效 JSON — 拒絕寫入") from exc
