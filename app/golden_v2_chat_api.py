"""Private V2 chat entry: source preview/intake/status, with no paid endpoints.

Run separately from V1. The same isolated V2 ledger is used by the chat client,
owner console and intake daemon. No provider credentials are needed here.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, Header, HTTPException
from app.jobs.drive_api import DriveApiError, drive_health
from app.golden_v2_routes import _authorized_database, router

app = FastAPI(title="Golden Transcript V2 Chat Entry", docs_url=None, redoc_url=None, openapi_url=None)
allowed = {
    "/api/v1/golden-v2/jobs", "/api/v1/golden-v2/jobs/{job_id}",
    "/api/v1/golden-v2/link-preview", "/api/v1/golden-v2/link-batches",
    "/api/v1/golden-v2/link-batches/{batch_id}",
}
limited = APIRouter()
limited.routes.extend(route for route in router.routes
                      if route.path in allowed and not (route.path.endswith('/jobs') and 'POST' in route.methods))
app.include_router(limited)


def _revision() -> str:
    value = os.environ.get("COURSE_TRANSCRIPT_V2_RELEASE_SHA", "")
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else "UNVERIFIED"


@app.middleware("http")
async def private_response(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get('/health')
def health() -> dict:
    return {"status": "ok", "service": "golden-v2-chat", "release_sha": _revision()}


@app.get('/api/v1/golden-v2/service')
def service_status(authorization: str | None = Header(default=None)) -> dict:
    database = _authorized_database(authorization)
    heartbeat = database.parent.parent / 'runtime' / 'golden-v2-intake.heartbeat.json'
    worker = {"state": "UNVERIFIED", "updated_at": None}
    try:
        payload = json.loads(heartbeat.read_text())
        stamp = datetime.fromisoformat(payload['updated_at'])
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if (payload.get('service') == 'golden-v2-intake' and stamp.tzinfo is not None
                and 0 <= age <= 90):
            worker = {"state": payload['state'], "updated_at": payload['updated_at']}
        elif isinstance(payload.get('updated_at'), str):
            worker = {"state": "STALE_OR_STOPPED", "updated_at": payload['updated_at']}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {
        "schema": "golden-v2-chat-service-v1", "release_sha": _revision(),
        "capabilities": {"drive_link_preview": True, "batch_source_intake": True,
                         "job_and_batch_status": True, "paid_execution": False,
                         "drive_publication": False},
        "intake_worker": worker, "cost_estimates": "OPERATOR_SUPPLIED_UNTIL_TRUSTED_PRICING",
        "golden_acceptance": "REQUIRED_SEPARATELY",
    }


@app.get('/api/v1/golden-v2/drive-health')
def drive_connection_status(authorization: str | None = Header(default=None)) -> dict:
    _authorized_database(authorization)
    try:
        return drive_health()
    except DriveApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
