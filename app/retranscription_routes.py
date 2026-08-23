"""Guarded API routes for ASR quality review and retranscription candidates."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import JOBS_DIR, _cost_config, _mutation_actor, _store
from app.jobs.retranscription_candidates import (
    RetranscriptionCandidateStore,
    chunk_source_sha256,
    config_sha256,
    request_idempotency_key,
)
from app.jobs.store import JobConflict, JobNotFound
from app.jobs.strategy import normalize_processing_strategy
from app.providers.asr_quality import analyze_job

router = APIRouter(prefix="/api/v1", tags=["asr-retranscription"])


class RetranscriptionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    chunk_index: int = Field(ge=0, le=9999)


class CreateRetranscriptionCandidateRequest(RetranscriptionPreviewRequest):
    confirmed_estimated_cost_usd: Decimal = Field(gt=0)
    force: bool = False


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _candidate_store() -> RetranscriptionCandidateStore:
    return RetranscriptionCandidateStore(_store())


def _job(job_id: str) -> dict[str, Any]:
    try:
        return _store().get_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _chunk_window(job_dir: Path, chunk_index: int) -> tuple[int, int]:
    plan = _read_json(job_dir / "chunk-plan.json", {})
    chunks = plan.get("chunks", []) if isinstance(plan, dict) else []
    if isinstance(chunks, list):
        for item in chunks:
            if not isinstance(item, dict):
                continue
            try:
                if int(item.get("chunk_index", -1)) == chunk_index:
                    start_ms = int(item["source_start_ms"])
                    end_ms = int(item["source_end_ms"])
                    if end_ms > start_ms:
                        return start_ms, end_ms
            except (KeyError, TypeError, ValueError):
                continue
    manifest = _read_json(
        job_dir / "chunks" / f"chunk-{chunk_index:03d}" / "manifest.json", {}
    )
    try:
        start_ms = int(manifest["source_start_ms"])
        end_ms = int(manifest["source_end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Chunk window not found") from exc
    if end_ms <= start_ms:
        raise HTTPException(status_code=409, detail="Chunk window is invalid")
    return start_ms, end_ms


def _quality_entry(job_dir: Path, chunk_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    report = analyze_job(job_dir)
    chunks = report.get("chunks", []) if isinstance(report, dict) else []
    if not isinstance(chunks, list):
        chunks = []
    for item in chunks:
        if isinstance(item, dict) and int(item.get("chunk_index", -1)) == chunk_index:
            return report, item
    raise HTTPException(status_code=404, detail="Chunk quality evidence not found")


def _estimate(job: dict[str, Any], start_ms: int, end_ms: int) -> dict[str, Any]:
    strategy = normalize_processing_strategy(job.get("processing_strategy"))
    config = _cost_config().for_processing_strategy(strategy)
    duration_ms = end_ms - start_ms
    billable_minutes = (
        Decimal(duration_ms) / Decimal("60000")
    ).quantize(Decimal("0.01"), rounding=ROUND_UP)
    base = billable_minutes * config.chirp_usd_per_minute
    estimated = (
        base
        * config.chirp_retry_and_overlap_multiplier
        * config.contingency_multiplier
    ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    return {
        "duration_ms": duration_ms,
        "billable_minutes": str(billable_minutes),
        "processing_strategy": strategy,
        "chirp_usd_per_minute": str(config.chirp_usd_per_minute),
        "estimated_cost_usd": str(estimated),
        "estimated_cost_twd": str(config.usd_as_twd(estimated)),
        "pricing_version": config.pricing_version,
        "project_limit_usd": str(config.project_limit_usd),
    }


def _candidate_committed_usd() -> Decimal:
    """Reserve queued work and retain cost once a provider submission occurred.

    Rejecting a completed result does not undo provider billing. Failed/stale
    candidates are excluded only when they never reached provider submission.
    """
    candidates = _candidate_store()
    with candidates.jobs.connect() as connection:
        rows = connection.execute(
            """
            SELECT confirmed_cost_usd FROM asr_retranscription_candidates
            WHERE status IN ('queued','submitted','processing','completed','applied')
               OR submitted_at IS NOT NULL
            """
        ).fetchall()
    return sum(
        (Decimal(str(row["confirmed_cost_usd"] or "0")) for row in rows),
        Decimal("0"),
    )


def _budget_snapshot(estimate: Decimal | None = None) -> dict[str, str]:
    config = _cost_config()
    jobs_summary = _store().cost_summary(config.project_limit_usd)
    job_committed = Decimal(str(jobs_summary["committed_estimated_cost_usd"]))
    candidate_committed = _candidate_committed_usd()
    committed = job_committed + candidate_committed
    after = committed + (estimate or Decimal("0"))
    return {
        "project_limit_usd": str(config.project_limit_usd),
        "job_committed_estimated_cost_usd": str(job_committed),
        "candidate_committed_estimated_cost_usd": str(candidate_committed),
        "committed_before_request_usd": str(committed),
        "committed_after_request_usd": str(after),
        "remaining_after_request_usd": str(max(Decimal("0"), config.project_limit_usd - after)),
    }


def _existing_candidate(
    *,
    job_id: str,
    source_revision: int,
    chunk_index: int,
    source_chunk_sha256: str,
    language_code: str,
    processing_strategy: str,
) -> dict[str, Any] | None:
    """Find the exact idempotent request so preview never double-reserves it."""
    digest = config_sha256(
        language_code=language_code,
        processing_strategy=processing_strategy,
    )
    key = request_idempotency_key(
        job_id=job_id,
        source_revision=source_revision,
        chunk_index=chunk_index,
        source_chunk_sha256=source_chunk_sha256,
        recognizer_config_sha256=digest,
    )
    candidates = _candidate_store()
    with candidates.jobs.connect() as connection:
        row = connection.execute(
            "SELECT * FROM asr_retranscription_candidates WHERE idempotency_key=?",
            (key,),
        ).fetchone()
    return dict(row) if row is not None else None


def _safe_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "source_revision": int(row["source_revision"]),
        "chunk_index": int(row["chunk_index"]),
        "recognizer": row["recognizer"],
        "language_code": row["language_code"],
        "processing_strategy": row["processing_strategy"],
        "estimated_cost_usd": row["estimated_cost_usd"],
        "confirmed_cost_usd": row["confirmed_cost_usd"],
        "pricing_version": row["pricing_version"],
        "status": row["status"],
        "requested_by": row["requested_by"],
        "requested_at": row["requested_at"],
        "updated_at": row["updated_at"],
        "submitted_at": row["submitted_at"],
        "completed_at": row["completed_at"],
        "failed_at": row["failed_at"],
        "rejected_at": row["rejected_at"],
        "error_kind": row["error_kind"],
        "error_safe_message": row["error_safe_message"],
    }


def _preview(job_id: str, expected_revision: int, chunk_index: int) -> dict[str, Any]:
    job = _job(job_id)
    if int(job["revision"]) != expected_revision:
        raise HTTPException(status_code=409, detail="Job revision changed; reload before retranscription")
    if str(job["status"]) not in {"completed", "awaiting_review", "failed"}:
        raise HTTPException(
            status_code=409,
            detail="Only completed, awaiting-review, or failed jobs can create a candidate",
        )
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job artifacts not found")
    start_ms, end_ms = _chunk_window(job_dir, chunk_index)
    _report, quality = _quality_entry(job_dir, chunk_index)
    estimate = _estimate(job, start_ms, end_ms)
    try:
        source_sha = chunk_source_sha256(job_dir, chunk_index)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    amount = Decimal(str(estimate["estimated_cost_usd"]))
    strategy = str(estimate["processing_strategy"])
    language_code = str(job.get("language_code") or "cmn-Hant-TW")
    existing = _existing_candidate(
        job_id=job_id,
        source_revision=int(job["revision"]),
        chunk_index=chunk_index,
        source_chunk_sha256=source_sha,
        language_code=language_code,
        processing_strategy=strategy,
    )
    return {
        "job_id": job_id,
        "job_revision": int(job["revision"]),
        "chunk_index": chunk_index,
        "source_start_ms": start_ms,
        "source_end_ms": end_ms,
        "source_chunk_sha256": source_sha,
        "quality": {
            "severity": quality.get("severity"),
            "score": quality.get("score"),
            "reasons": list(quality.get("reasons") or []),
            "metrics": quality.get("metrics", {}),
        },
        "recommended_for_retranscription": str(quality.get("severity")) in {"medium", "high"},
        "estimate": estimate,
        "budget": _budget_snapshot(None if existing is not None else amount),
        "existing_candidate": _safe_candidate(existing) if existing is not None else None,
        "new_cost_reservation_required": existing is None,
        "paid_operation_started": False,
    }


@router.get("/jobs/{job_id}/asr-quality")
def get_asr_quality(job_id: str) -> dict[str, Any]:
    _job(job_id)
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job artifacts not found")
    return analyze_job(job_dir)


@router.post("/jobs/{job_id}/retranscription-candidates/preview")
def preview_retranscription_candidate(
    job_id: str,
    payload: RetranscriptionPreviewRequest,
) -> dict[str, Any]:
    return _preview(job_id, payload.expected_revision, payload.chunk_index)


@router.post("/jobs/{job_id}/retranscription-candidates", status_code=201)
def create_retranscription_candidate(
    job_id: str,
    payload: CreateRetranscriptionCandidateRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    preview = _preview(job_id, payload.expected_revision, payload.chunk_index)
    quality = preview["quality"]
    if not preview["recommended_for_retranscription"] and not payload.force:
        raise HTTPException(
            status_code=409,
            detail=(
                "This chunk is not medium/high severity. Reload quality evidence or "
                "explicitly use force=true after operator review."
            ),
        )
    estimate = Decimal(str(preview["estimate"]["estimated_cost_usd"]))
    if payload.confirmed_estimated_cost_usd != estimate:
        raise HTTPException(
            status_code=409,
            detail="Confirmed amount does not match the latest retranscription estimate",
        )
    budget = preview["budget"]
    if Decimal(str(budget["committed_after_request_usd"])) > Decimal(
        str(budget["project_limit_usd"])
    ):
        raise HTTPException(status_code=409, detail="Retranscription would exceed project cost limit")
    job = _job(job_id)
    candidates = _candidate_store()
    try:
        row, created = candidates.create(
            job_id=job_id,
            expected_revision=payload.expected_revision,
            chunk_index=payload.chunk_index,
            source_chunk_sha256=str(preview["source_chunk_sha256"]),
            language_code=str(job.get("language_code") or "cmn-Hant-TW"),
            processing_strategy=str(preview["estimate"]["processing_strategy"]),
            estimated_cost_usd=estimate,
            confirmed_cost_usd=payload.confirmed_estimated_cost_usd,
            pricing_version=str(preview["estimate"]["pricing_version"]),
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "candidate": _safe_candidate(row),
        "created": created,
        "quality": quality,
        "paid_operation_started": False,
        "worker_will_require_durable_candidate_lease": True,
    }


@router.get("/jobs/{job_id}/retranscription-candidates")
def list_retranscription_candidates(job_id: str) -> dict[str, Any]:
    _job(job_id)
    rows = _candidate_store().list_for_job(job_id)
    return {"candidates": [_safe_candidate(row) for row in rows]}


@router.get("/jobs/{job_id}/retranscription-candidates/{candidate_id}")
def get_retranscription_candidate(job_id: str, candidate_id: str) -> dict[str, Any]:
    _job(job_id)
    try:
        row = _candidate_store().get(candidate_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if str(row["job_id"]) != job_id:
        raise HTTPException(status_code=404, detail="Retranscription candidate not found")
    comparison = None
    if str(row["status"]) in {"completed", "rejected", "applied", "stale"}:
        path = JOBS_DIR / job_id / str(row["candidate_relpath"]) / "comparison.json"
        value = _read_json(path, None)
        comparison = value if isinstance(value, dict) else None
    return {"candidate": _safe_candidate(row), "comparison": comparison}


@router.post("/jobs/{job_id}/retranscription-candidates/{candidate_id}/reject")
def reject_retranscription_candidate(
    job_id: str,
    candidate_id: str,
    payload: CandidateDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    candidates = _candidate_store()
    try:
        current = candidates.get(candidate_id)
        if str(current["job_id"]) != job_id:
            raise JobNotFound(candidate_id)
        row = candidates.reject(
            candidate_id=candidate_id,
            expected_job_revision=payload.expected_revision,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"candidate": _safe_candidate(row), "accepted_artifacts_mutated": False}
