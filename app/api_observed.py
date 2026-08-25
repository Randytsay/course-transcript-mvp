"""Production API entry point with cancellation, performance and subtitle tools."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor
from app.api_final import app
from app.jobs.cancellation import (
    CancellationConflict,
    CancellationNotFound,
    request_cancellation,
)
from app.jobs.performance_enhanced import build_performance_summary, write_performance_reports
from app.subtitles.editor import router as subtitle_router
from app.subtitles.ai_review import router as ai_subtitle_review_router

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
DATABASE_PATH = DATA_DIR / "course-transcript.db"
router = APIRouter()


class CancelJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(default="使用者要求取消", min_length=1, max_length=300)
    cleanup_mode: Literal["preserve", "temporary"] = "preserve"


@router.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    payload: CancelJobRequest,
    request: Request,
) -> dict[str, object]:
    actor = _mutation_actor(request)
    try:
        record = request_cancellation(
            DATABASE_PATH,
            DATA_DIR,
            job_id=job_id,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            cleanup_mode=payload.cleanup_mode,
            actor=actor,
        )
    except CancellationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CancellationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "jobId": record["id"],
        "status": record["status"],
        "revision": record["revision"],
        "stageDetail": record.get("stage_detail"),
        "estimatedAccruedCostUsd": record.get("reserved_cost_usd") or "0",
        "cleanupMode": payload.cleanup_mode,
        "providerCancellationBestEffort": record["status"] == "cancelling",
        "warning": (
            "已送出的雲端操作可能在取消生效前完成並產生費用；"
            "Cloud Billing 為最終帳務依據。"
        ),
    }


@router.get("/api/v1/jobs/{job_id}/performance")
def get_job_performance(job_id: str) -> dict[str, object]:
    try:
        return build_performance_summary(DATABASE_PATH, DATA_DIR, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.get("/api/v1/jobs/{job_id}/performance-report.{report_format}")
def get_job_performance_report(job_id: str, report_format: str) -> FileResponse:
    if report_format not in {"json", "csv", "html"}:
        raise HTTPException(status_code=404, detail="Unsupported report format")
    try:
        summary = build_performance_summary(DATABASE_PATH, DATA_DIR, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    job_dir = DATA_DIR / "jobs" / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job output directory not found")
    paths = write_performance_reports(job_dir, summary)
    media_types = {
        "json": "application/json",
        "csv": "text/csv; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }
    return FileResponse(
        paths[report_format],
        media_type=media_types[report_format],
        filename=f"{job_id}-performance-report.{report_format}",
    )


app.include_router(router)
app.include_router(subtitle_router)
app.include_router(ai_subtitle_review_router)
