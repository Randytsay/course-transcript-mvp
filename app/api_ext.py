"""Production API entry point with reviewed live and billing extensions."""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import (
    _mutation_actor,
    _store,
    app,
)
from app.jobs import JobConflict, JobNotFound
from app.live_features import router as live_router


class CreateJobWithParallelismRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    language_code: str = Field(default="cmn-Hant-TW", pattern=r"^[A-Za-z-]{2,24}$")
    profile: Literal["highest_accuracy"] = "highest_accuracy"
    enable_gemini_correction: bool = True
    enable_subtitles: bool = True
    require_human_review: bool = True
    chirp_max_parallel_chunks: int = Field(default=3, ge=1, strict=True)


class CreateBatchWithParallelismRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    language_code: str = Field(default="cmn-Hant-TW", pattern=r"^[A-Za-z-]{2,24}$")
    profile: Literal["highest_accuracy"] = "highest_accuracy"
    enable_gemini_correction: bool = True
    enable_subtitles: bool = True
    require_human_review: bool = True
    chirp_max_parallel_chunks: int = Field(default=3, ge=1, strict=True)


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


# The feature branch originally placed prototype handlers directly in app.api.
# Remove only reviewed replacements. GET /jobs and other stable endpoints remain.
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


@app.post("/api/v1/batches", status_code=201)
def create_batch_with_parallelism(
    payload: CreateBatchWithParallelismRequest,
    request: Request,
) -> dict[str, object]:
    actor = _mutation_actor(request)
    parallelism = _validate_parallelism(payload.chirp_max_parallel_chunks)
    try:
        result = _store().create_preflight_batch(
            batch_preview_id=payload.batch_preview_id,
            language_code=payload.language_code,
            profile=payload.profile,
            enable_gemini_correction=payload.enable_gemini_correction,
            enable_subtitles=payload.enable_subtitles,
            require_human_review=payload.require_human_review,
            chirp_max_parallel_chunks=parallelism,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    batch = result["batch"]
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "item_count": batch["item_count"],
        "job_ids": [job["id"] for job in result["jobs"]],
        "chirp_max_parallel_chunks": parallelism,
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
    try:
        record = _store().create_preflight_job(
            preview_id=payload.preview_id,
            language_code=payload.language_code,
            profile=payload.profile,
            enable_gemini_correction=payload.enable_gemini_correction,
            enable_subtitles=payload.enable_subtitles,
            require_human_review=payload.require_human_review,
            chirp_max_parallel_chunks=parallelism,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job_id": record["id"],
        "status": record["status"],
        "chirp_max_parallel_chunks": parallelism,
        "created_at": record["created_at"],
        "paid_operation_started": False,
        "next_action": "等待本機 preflight 取得音訊長度與預估費用",
    }


app.include_router(live_router)
