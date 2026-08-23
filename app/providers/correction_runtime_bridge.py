"""Production worker bridge to the new provider router.

Replaces app.providers.correct_text_hardened for NEW jobs that carry a
non-empty correction_provider. Legacy GEMINI_FIRST / M3_FIRST jobs (those
without per-job correction fields) keep going through
correct_text_hardened unchanged.

Contract for the dispatch chain (see app/pipeline/dynamic_worker_hardened.py):
- Reads persisted per-job correction selection from the SQLite job row
- Builds the orchestrator + run store against the production DB
- For REALTIME: dispatches to the configured provider and persists
  corrections + a waiting_ai_batch run row (REALTIME-shaped)
- For BATCH: submits once via orchestrator (idempotent on
  (job_id, source_revision, request_hash)); the recovery scheduler takes
  over to poll and ingest. Worker lease is released; no 24-hour held
  lease while waiting for batch completion.
- The legacy correct_text_hardened path keeps functioning for legacy
  jobs that have empty correction_provider in the DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


# Invoked by dynamic_worker_hardened; reads environment that the worker
# already populated from the persisted job row.
def run_module(*, ctx: dict[str, Any]) -> dict[str, Any]:
    provider = (ctx.get("correction_provider") or "").strip()
    if not provider:
        # Defensive: dynamic_worker_hardened should have routed legacy jobs
        # to correct_text_hardened instead. Fail-closed.
        raise RuntimeError(
            "correction_runtime_bridge invoked without a per-job provider; "
            "this is a worker routing bug, not a user error"
        )

    from app.providers.correction.base import ProviderError
    from app.providers.correction.orchestrator import (
        CorrectionOrchestrator,
        JobCorrectionSpec,
    )
    from app.providers.correction.batch_state import AICorrectionRunStore

    job_id = ctx["job_id"]
    profile_id = ctx.get("correction_provider_profile_id") or ""
    model = ctx.get("correction_model") or ""
    mode = (ctx.get("correction_execution_mode") or "REALTIME").upper()
    fallback = ctx.get("correction_fallback_policy") or "RAW_CHIRP_FALLBACK"

    spec = JobCorrectionSpec(
        job_id=job_id, provider=provider, provider_profile_id=profile_id,
        model=model, execution_mode=mode, fallback_policy=fallback,
        source_revision=ctx.get("source_revision") or "",
        source_sha256=ctx.get("source_sha256") or "",
    )

    orch = CorrectionOrchestrator(
        run_store=_open_run_store(ctx),
        client_factory=_make_client_factory(ctx),
    )

    segments = ctx.get("segments") or []
    glossary = ctx.get("glossary") or []

    if mode == "BATCH":
        # submit-once idempotent: orchestrator handles durable dedup
        result = orch.submit_batch(spec, segments, glossary)
        ctx["correction_status"] = result["status"]           # submitted
        ctx["correction_provider_job_id"] = result["provider_job_id"]
        ctx["correction_resubmitted"] = result["resubmitted"]
        ctx["correction_job_status"] = "waiting_ai_batch"
        ctx["lease_released"] = True   # scheduler owns the wait
        return ctx

    # REALTIME
    try:
        result = orch.correct_realtime(spec, segments, glossary)
        ctx["correction_status"] = "completed_realtime"
        ctx["correction_corrections"] = result["corrections"]
        ctx["correction_prompt_version"] = result["prompt_version"]
    except ProviderError as exc:
        if fallback == "RAW_CHIRP_FALLBACK":
            ctx["correction_status"] = "fallback_raw_chirp"
            ctx["correction_error_kind"] = exc.kind
            ctx["correction_error_safe_message"] = exc.safe_message
        else:
            raise
    return ctx


# ---------------------------------------------------------------------------
# helpers (kept in this file because they touch production env directly)
# ---------------------------------------------------------------------------

def _db_path(ctx: dict[str, Any]) -> Path:
    p = os.environ.get("COURSE_TRANSCRIPT_DATA_DIR")
    if p:
        return Path(p) / "course-transcript.db"
    # Fallback for tests.
    return Path(ctx.get("data_dir") or ".") / "course-transcript.db"


def _open_run_store(ctx: dict[str, Any]) -> AICorrectionRunStore:
    """Real production DB path; reads/writes ai_correction_runs via the
    canonical JobStore-managed connection pool."""
    from app.jobs.store import JobStore
    store = JobStore(_db_path(ctx))
    return AICorrectionRunStore(lambda: store.transaction())


def _make_client_factory(ctx: dict[str, Any]):
    """Return a (provider, profile_id) -> client resolver.

    Vertex uses the active Account Profile runtime credential (no API key).
    OpenRouter / MiniMax read the API key from the provider profile store.
    """
    from app.providers.correction.base import ProviderError
    from app.providers.correction.openrouter import OpenRouterCorrectionProvider
    from app.providers.correction.minimax import MiniMaxCorrectionProvider
    from app.providers.correction.registry import AIProviderProfileStore

    profiles_root = Path(os.environ.get(
        "AI_PROVIDER_PROFILES_DIR",
        "/run/ai-providers"))

    selected_model = (ctx.get("correction_model") or "").strip()

    def factory(provider: str, profile_id: str):
        if provider == "vertex":
            return _vertex_client(ctx, model=selected_model or None)
        prof_store = AIProviderProfileStore(profiles_root)
        return prof_store.build_client(
            profile_id, model=selected_model or None)
    return factory


def _vertex_client(ctx: dict[str, Any], *, model: str | None = None):
    """Build a Vertex client. Auth comes from GOOGLE_APPLICATION_CREDENTIALS
    (already pointed at /run/ai-runtime/gcp-sa.json by compose)."""
    from app.providers.correction.vertex import VertexCorrectionProvider
    if model:
        return VertexCorrectionProvider(model=model)
    return VertexCorrectionProvider()
