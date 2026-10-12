"""Disabled-by-default owner status and approval API for isolated Golden V2 tasks.

The bearer secret is for trusted server-to-server operator clients. Never put
it in browser JavaScript. Cloudflare Access may be added upstream, but the
origin endpoint always verifies its own independent secret.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.jobs.golden_v2_read import get_v2_status, list_v2_status
from app.jobs.golden_v2_ledger import STAGES, GoldenV2Ledger, V2Conflict

router = APIRouter(prefix="/api/v1/golden-v2", tags=["golden-v2-owner"])


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["QUALITY_3WAY", "SMART_2PLUS", "FLASH_CHIRP"]
    estimated_stage_cents: dict[str, StrictInt]
    confirm: Literal[True]


class JobApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    approved_cents: int = Field(ge=0)
    reference: str = Field(min_length=1, max_length=256)
    confirm: Literal[True]


class RepairApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    assessment_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    windows: list[dict[str, Any]] = Field(min_length=1, max_length=12)
    approved_cents: int = Field(ge=0)
    reference: str = Field(min_length=1, max_length=256)
    confirm: Literal[True]


def _authorized_database(authorization: str | None) -> Path:
    if os.environ.get("COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=404, detail="V2 status endpoint not enabled")
    secret = os.environ.get("COURSE_TRANSCRIPT_V2_STATUS_TOKEN", "")
    database = os.environ.get("COURSE_TRANSCRIPT_V2_LEDGER_PATH", "")
    if len(secret) < 32 or not database:
        raise HTTPException(status_code=503, detail="V2 owner API is not configured")
    expected = "Bearer " + secret
    # Prevent header spoofing from becoming an owner identity. No query tokens.
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Owner token required")
    path = Path(database)
    if not path.is_file():
        raise HTTPException(status_code=503, detail="V2 ledger unavailable")
    return path


def _authorized_write_database(authorization: str | None) -> tuple[Path, str, int]:
    if os.environ.get("COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=404, detail="V2 owner actions not enabled")
    secret = os.environ.get("COURSE_TRANSCRIPT_V2_WRITE_TOKEN", "")
    database = os.environ.get("COURSE_TRANSCRIPT_V2_LEDGER_PATH", "")
    actor = os.environ.get("COURSE_TRANSCRIPT_V2_OWNER_ACTOR", "").strip()
    try:
        max_approval_cents = int(os.environ.get(
            "COURSE_TRANSCRIPT_V2_MAX_APPROVAL_CENTS", "1000"
        ))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="V2 owner actions are not configured") from exc
    if (len(secret) < 32 or not actor or not database
            or max_approval_cents <= 0):
        raise HTTPException(status_code=503, detail="V2 owner actions are not configured")
    expected = "Bearer " + secret
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Owner token required")
    path = Path(database)
    if not path.is_file():
        raise HTTPException(status_code=503, detail="V2 ledger unavailable")
    return path, actor, max_approval_cents


def _read_error(exc: sqlite3.Error) -> HTTPException:
    # No raw SQLite errors or filesystem details in HTTP responses.
    return HTTPException(status_code=503, detail="V2 ledger cannot be read")


@router.get("/jobs")
def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    authorization: str | None = Header(default=None),
) -> dict:
    db = _authorized_database(authorization)
    try:
        return {"jobs": list_v2_status(db, limit=limit)}
    except sqlite3.Error as exc:
        raise _read_error(exc) from exc


@router.get("/jobs/{job_id}")
def job_status(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    db = _authorized_database(authorization)
    try:
        result = get_v2_status(db, job_id=job_id)
    except sqlite3.Error as exc:
        raise _read_error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Golden V2 job not found")
    return result


@router.post("/jobs", status_code=201)
def create_job(
    body: JobCreateRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    """Register a source identity in the local ledger; never start providers.

    The trusted caller supplies a digest previously established by its own
    source/normalization preflight. This endpoint cannot attest media bytes.
    """
    database, _, max_approval_cents = _authorized_write_database(authorization)
    estimates = body.estimated_stage_cents
    if (set(estimates) != set(STAGES)
            or any(type(value) is not int or value < 0 for value in estimates.values())):
        raise HTTPException(status_code=422, detail="Invalid per-stage cost estimate")
    estimate_total = sum(estimates.values())
    if estimate_total > max_approval_cents:
        raise HTTPException(status_code=422, detail="Estimate exceeds configured per-job ceiling")
    try:
        ledger = GoldenV2Ledger.open_existing(database)
        job_id = ledger.create(
            source_sha=body.source_sha256, mode=body.mode,
            estimated_stage_cents=estimates,
        )
        return {
            "job_id": job_id, "status": "AWAITING_APPROVAL", "revision": 0,
            "estimated_cents": estimate_total,
            "provider_calls_started": False, "drive_mutation_started": False,
        }
    except (V2Conflict, ValueError, sqlite3.Error) as exc:
        raise _mutation_error(exc) from exc


def _mutation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, V2Conflict):
        if str(exc) == "unknown job":
            return HTTPException(status_code=404, detail="Golden V2 job not found")
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail="Invalid owner action")
    if isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=503, detail="V2 ledger cannot be updated")
    return HTTPException(status_code=500, detail="V2 owner action failed")


@router.post("/jobs/{job_id}/approval")
def approve_job(
    job_id: str,
    body: JobApprovalRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    database, actor, max_approval_cents = _authorized_write_database(authorization)
    if body.approved_cents > max_approval_cents:
        raise HTTPException(status_code=422, detail="Approval exceeds configured per-job ceiling")
    try:
        ledger = GoldenV2Ledger.open_existing(database)
        ledger.approve(
            job_id, revision=body.revision, approved_cents=body.approved_cents,
            reference=body.reference, actor=actor,
        )
        return {"job_id": job_id, "status": "QUEUED", "revision": body.revision + 1}
    except (V2Conflict, ValueError, sqlite3.Error) as exc:
        raise _mutation_error(exc) from exc


@router.post("/jobs/{job_id}/repair-approval")
def approve_repair_plan(
    job_id: str,
    body: RepairApprovalRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    database, actor, max_approval_cents = _authorized_write_database(authorization)
    if body.approved_cents > max_approval_cents:
        raise HTTPException(status_code=422, detail="Repair approval exceeds configured per-job ceiling")
    try:
        ledger = GoldenV2Ledger.open_existing(database)
        result = ledger.approve_chirp_repair_plan(
            job_id, expected_revision=body.revision,
            assessment_sha=body.assessment_sha, windows=body.windows,
            approved_cents=body.approved_cents, reference=body.reference,
            actor=actor,
        )
        return {**result, "status": "APPROVED"}
    except (V2Conflict, ValueError, sqlite3.Error) as exc:
        raise _mutation_error(exc) from exc
