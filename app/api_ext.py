"""Production API entry point with reviewed live and billing extensions."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor, _store, app
from app.jobs import JobConflict, JobNotFound, normalize_output_formats
from app.jobs.content_context import MAX_DOCUMENT_CONTEXT_CHARS
from app.jobs.correction_policy import (
    DEFAULT_CORRECTION_POLICY,
    get_job_correction_policy,
    set_batch_correction_policy,
    set_job_correction_policy,
)
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY, DYNAMIC_BATCHING, STANDARD_BATCH
from app.live_error import safe_chunk_error
from app.providers.correction.registry import LEGACY_MINIMAX_PROFILE_ID
from app.providers.correction_routing import M3QuotaState
from app.providers.minimax_quota import MiniMaxQuotaClient
import app.live_features as live_features

live_features.safe_chunk_error = safe_chunk_error
live_router = live_features.router


class CreateJobWithParallelismRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    language_code: str = Field(default="cmn-Hant-TW", pattern=r"^[A-Za-z-]{2,24}$")
    profile: Literal["highest_accuracy"] = "highest_accuracy"
    enable_gemini_correction: bool = True
    enable_subtitles: bool = True
    require_human_review: bool = False
    processing_strategy: Literal[DYNAMIC_BATCHING, STANDARD_BATCH] = DEFAULT_PROCESSING_STRATEGY
    chirp_max_parallel_chunks: int = Field(default=3, ge=1, strict=True)
    output_formats: list[str] = Field(default_factory=lambda: ["srt", "txt", "csv"], min_length=1, max_length=7)
    content_mode: Literal["general", "dacheng_buddhist"] = "general"
    document_context: str = Field(default="", max_length=MAX_DOCUMENT_CONTEXT_CHARS)
    correction_policy: Literal["GEMINI_FIRST", "M3_FIRST"] = DEFAULT_CORRECTION_POLICY


class CreateBatchWithParallelismRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    language_code: str = Field(default="cmn-Hant-TW", pattern=r"^[A-Za-z-]{2,24}$")
    profile: Literal["highest_accuracy"] = "highest_accuracy"
    enable_gemini_correction: bool = True
    enable_subtitles: bool = True
    require_human_review: bool = False
    processing_strategy: Literal[DYNAMIC_BATCHING, STANDARD_BATCH] = DEFAULT_PROCESSING_STRATEGY
    chirp_max_parallel_chunks: int = Field(default=3, ge=1, strict=True)
    output_formats: list[str] = Field(default_factory=lambda: ["srt", "txt", "csv"], min_length=1, max_length=7)
    content_mode: Literal["general", "dacheng_buddhist"] = "general"
    document_context: str = Field(default="", max_length=MAX_DOCUMENT_CONTEXT_CHARS)
    correction_policy: Literal["GEMINI_FIRST", "M3_FIRST"] = DEFAULT_CORRECTION_POLICY


def _parallelism_limit() -> int:
    try:
        value = int(os.environ.get("CHIRP_MAX_PARALLEL_CHUNKS_LIMIT", "5"))
    except ValueError as exc:
        raise RuntimeError("CHIRP_MAX_PARALLEL_CHUNKS_LIMIT must be an integer") from exc
    return max(1, min(value, 32))


def _validate_parallelism(value: int) -> int:
    limit = _parallelism_limit()
    if value > limit:
        raise HTTPException(
            status_code=422,
            detail=f"chirp_max_parallel_chunks must be between 1 and {limit}",
        )
    return value


def _m3_enabled() -> bool:
    return os.environ.get("MINIMAX_M3_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _production_correction_router_fields(
    *, policy: str, correction_enabled: bool
) -> dict[str, object]:
    """Bridge the existing M3_FIRST UI contract to the shared provider router.

    M3_FIRST is allowed to be saved while M3 is disabled. In that state we
    deliberately leave provider fields blank so the existing fail-closed Gemini
    path remains in force. Only jobs created after the production M3 flag is
    explicitly enabled are pinned to the windowed MiniMax router.
    """
    if correction_enabled and policy == "M3_FIRST" and _m3_enabled():
        return {
            "correction_provider": "minimax",
            "correction_provider_profile_id": LEGACY_MINIMAX_PROFILE_ID,
            "correction_model": os.environ.get("MINIMAX_M3_MODEL", "MiniMax-M3"),
            "correction_execution_mode": "REALTIME",
            "correction_fallback_policy": "RAW_CHIRP_FALLBACK",
            "pricing_snapshot": {},
        }
    return {}


_REPLACED_READ_PATHS = {
    "/api/v1/jobs/{job_id}/chunks",
    "/api/v1/jobs/{job_id}/chunks/{chunk_index}/transcript",
    "/api/v1/jobs/{job_id}/live-cost",
    "/api/v1/billing/summary",
}


def _keep_route(route: object) -> bool:
    path = getattr(route, "path", None)
    methods = set(getattr(route, "methods", set()) or set())
    if path in _REPLACED_READ_PATHS:
        return False
    if path == "/api/v1/batches" and "POST" in methods:
        return False
    if path == "/api/v1/jobs" and "POST" in methods:
        return False
    return True


app.router.routes = [route for route in app.router.routes if _keep_route(route)]


@app.get("/api/v1/correction/provider-status")
def correction_provider_status() -> dict[str, object]:
    """Expose safe capability/quota fields; never expose provider credentials."""
    quota_live_check = _m3_enabled() and os.environ.get(
        "MINIMAX_M3_QUOTA_CHECK_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    quota = None
    if quota_live_check:
        quota = MiniMaxQuotaClient().get_quota(force_refresh=True)
    key_file = Path(os.environ.get("MINIMAX_API_KEY_FILE", "/run/secrets/minimax-api-key"))
    try:
        minimax_configured = key_file.is_file() and key_file.stat().st_size > 0
    except OSError:
        minimax_configured = False
    return {
        "default_policy": DEFAULT_CORRECTION_POLICY,
        "m3_enabled": _m3_enabled(),
        "gemini_model": "gemini-3.7-flash",
        "m3_model": os.environ.get("MINIMAX_M3_MODEL", "MiniMax-M3"),
        "minimax_configured": minimax_configured,
        "quota_live_check": quota_live_check,
        "quota_state": quota.state.value if quota else M3QuotaState.UNKNOWN.value,
        "quota_checked_at": quota.checked_at if quota else None,
        "quota_source_pool": quota.source_pool if quota else None,
        "note": (
            "M3 policy may be persisted, but routing remains fail-closed: unknown "
            "quota always selects Gemini 3.7."
        ),
    }


@app.get("/api/v1/jobs/{job_id}/correction-policy")
def get_correction_policy(job_id: str) -> dict[str, object]:
    try:
        _store().get_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "requested_policy": get_job_correction_policy(_store(), job_id),
        "m3_enabled": _m3_enabled(),
    }


@app.post("/api/v1/batches", status_code=201)
def create_batch_with_parallelism(
    payload: CreateBatchWithParallelismRequest,
    request: Request,
) -> dict[str, object]:
    actor = _mutation_actor(request)
    parallelism = _validate_parallelism(payload.chirp_max_parallel_chunks)
    router_fields = _production_correction_router_fields(
        policy=payload.correction_policy,
        correction_enabled=payload.enable_gemini_correction,
    )
    try:
        result = _store().create_preflight_batch(
            batch_preview_id=payload.batch_preview_id,
            language_code=payload.language_code,
            profile=payload.profile,
            enable_gemini_correction=payload.enable_gemini_correction,
            enable_subtitles=payload.enable_subtitles,
            require_human_review=payload.require_human_review,
            processing_strategy=payload.processing_strategy,
            chirp_max_parallel_chunks=parallelism,
            output_formats=payload.output_formats,
            content_mode=payload.content_mode,
            document_context=payload.document_context,
            actor=actor,
            **router_fields,
        )
        job_ids = [job["id"] for job in result["jobs"]]
        policy = set_batch_correction_policy(
            _store(),
            job_ids=job_ids,
            policy=payload.correction_policy,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    batch = result["batch"]
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "item_count": batch["item_count"],
        "processing_strategy": batch["processing_strategy"],
        "correction_policy": policy,
        "job_ids": job_ids,
        "chirp_max_parallel_chunks": parallelism,
        "output_formats": normalize_output_formats(payload.output_formats),
        "content_mode": payload.content_mode,
        "created_at": batch["created_at"],
        "paid_operation_started": False,
        "next_action": "等待各檔案本機 preflight 取得音訊長度與批次預估費用",
    }


@app.post("/api/v1/jobs", status_code=201)
def create_job_with_parallelism(
    payload: CreateJobWithParallelismRequest,
    request: Request,
) -> dict[str, object]:
    actor = _mutation_actor(request)
    parallelism = _validate_parallelism(payload.chirp_max_parallel_chunks)
    router_fields = _production_correction_router_fields(
        policy=payload.correction_policy,
        correction_enabled=payload.enable_gemini_correction,
    )
    try:
        record = _store().create_preflight_job(
            preview_id=payload.preview_id,
            language_code=payload.language_code,
            profile=payload.profile,
            enable_gemini_correction=payload.enable_gemini_correction,
            enable_subtitles=payload.enable_subtitles,
            require_human_review=payload.require_human_review,
            processing_strategy=payload.processing_strategy,
            chirp_max_parallel_chunks=parallelism,
            output_formats=payload.output_formats,
            content_mode=payload.content_mode,
            document_context=payload.document_context,
            actor=actor,
            **router_fields,
        )
        policy = set_job_correction_policy(
            _store(),
            job_id=record["id"],
            policy=payload.correction_policy,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job_id": record["id"],
        "status": record["status"],
        "processing_strategy": record["processing_strategy"],
        "correction_policy": policy,
        "chirp_max_parallel_chunks": parallelism,
        "output_formats": normalize_output_formats(payload.output_formats),
        "created_at": record["created_at"],
        "paid_operation_started": False,
        "next_action": "等待本機 preflight 取得音訊長度與預估費用",
    }


app.include_router(live_router)
