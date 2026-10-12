"""Disabled-by-default owner status API for isolated Golden V2 tasks.

The bearer secret is for trusted server-to-server operator clients. Never put
it in browser JavaScript. Cloudflare Access may be added upstream, but the
origin endpoint always verifies its own independent secret.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query

from app.jobs.golden_v2_read import get_v2_status, list_v2_status

router = APIRouter(prefix="/api/v1/golden-v2", tags=["golden-v2-owner"])


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
