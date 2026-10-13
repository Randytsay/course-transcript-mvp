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
from typing import Literal
import sqlite3

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import Field
from pydantic import StrictBool
from app.jobs.drive_api import DriveApiError, drive_health
from app.golden_v2_routes import (_authorized_database, _authorized_write_database,
                                 LinkPreviewRequest, router, _mutation_error)
from app.pipeline.golden_v2_link_discovery import preview_drive_link
from app.pipeline.golden_v2_quote_intake import enqueue_quote, read_quote
from app.jobs.golden_v2_ledger import V2Conflict
from pydantic import BaseModel, ConfigDict, StrictInt
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_quote_intake import wire
from app.jobs.golden_v2_ledger import GoldenV2Ledger

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
from app.golden_v2_review_routes import router as review_router
app.include_router(review_router)


class QuoteRequest(LinkPreviewRequest):
    preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    file_id: str = Field(min_length=10, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    confirm: Literal[True]
    retry_failed: StrictBool = False


@app.post('/api/v1/golden-v2/quotes', status_code=202)
def request_quote(body: QuoteRequest, authorization: str | None = Header(default=None)):
    database, actor, _ = _authorized_write_database(authorization)
    try:
        preview = preview_drive_link(body.link, recursive=body.recursive)
        if preview['preview_sha256'] != body.preview_sha256:
            raise V2Conflict('Drive preview changed; refresh before quoting')
        return enqueue_quote(database=database, preview=preview, file_id=body.file_id, actor=actor,retry_failed=body.retry_failed)
    except DriveApiError as exc:
        raise HTTPException(status_code=422, detail='Drive source unavailable') from exc
    except (V2Conflict, ValueError, sqlite3.Error) as exc:
        raise _mutation_error(exc) from exc


@app.get('/api/v1/golden-v2/quotes/{quote_id}')
def quote_status(quote_id: str, authorization: str | None = Header(default=None)):
    database = _authorized_database(authorization)
    try:
        result = read_quote(database, quote_id)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail='Quote ledger unavailable') from exc
    if result is None:
        raise HTTPException(status_code=404, detail='Quote not found')
    return result


class PaidGrantRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt = Field(ge=0)
    quote_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    approved_cents: StrictInt = Field(gt=0)
    reference: str = Field(min_length=1,max_length=256)
    confirm: Literal[True]


@app.post('/api/v1/golden-v2/jobs/{job_id}/authorize-paid')
def authorize_paid(job_id: str, body: PaidGrantRequest, authorization: str | None = Header(default=None)):
    database, actor, ceiling = _authorized_write_database(authorization)
    if os.environ.get('COURSE_TRANSCRIPT_V2_PAID_API_ENABLED','').lower() != 'true':
        raise HTTPException(status_code=404, detail='Paid runtime not enabled')
    if not re.fullmatch(r'v2p_[0-9a-f]{32}',job_id) or body.approved_cents > ceiling:
        raise HTTPException(status_code=422,detail='Invalid or over-cap paid grant')
    job = database.parent/'jobs'/job_id
    try:
        quote_path = job/'trusted-quote.json'
        if job.is_symlink() or quote_path.is_symlink() or file_sha256(quote_path) != body.quote_sha256:
            raise V2Conflict('trusted quote hash changed')
        quote = json.loads(quote_path.read_text())
        from app.pipeline.golden_v2_pricing import assert_current
        assert_current()
        if quote['source_sha256'] != body.source_sha256 or quote['estimated_cents'] > body.approved_cents or quote['mode'] != 'FLASH_CHIRP':
            raise V2Conflict('quote/source/cap mismatch')
        ledger = GoldenV2Ledger.open_existing(database)
        state = ledger.status(job_id)
        if state['source_sha'] != body.source_sha256 or state['revision'] != body.revision or state['status'] != 'AWAITING_APPROVAL':
            raise V2Conflict('reviewed job state changed; query status')
        grant = {**body.model_dump(exclude={'confirm'}),'job_id':job_id,'actor':actor}
        store_immutable_bytes(job/'owner-paid-grant.json',wire(signed_grant(grant)).encode())
        ledger.approve(job_id,revision=body.revision,approved_cents=body.approved_cents,
                       reference=body.reference,actor=actor)
        return {'job_id':job_id,'status':'QUEUED','revision':body.revision+1,
                'source_sha256':body.source_sha256,'approved_cents':body.approved_cents}
    except (V2Conflict,ValueError,sqlite3.Error) as exc:
        raise _mutation_error(exc) from exc
    except (OSError,KeyError,TypeError) as exc:
        raise HTTPException(status_code=503,detail='Trusted quote or grant unavailable') from exc


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
    paid = {'state':'UNVERIFIED','updated_at':None}
    try:
        value=json.loads((database.parent.parent/'runtime/golden-v2-paid-executor.heartbeat.json').read_text())
        stamp=datetime.fromisoformat(value['updated_at'])
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        if value.get('service')=='golden-v2-paid-executor' and stamp.tzinfo is not None and 0<=age<=90:
            paid={'state':value['state'],'updated_at':value['updated_at']}
        else:
            paid={'state':'STALE_OR_STOPPED','updated_at':value.get('updated_at')}
    except (OSError,ValueError,KeyError,TypeError):
        pass
    paid_enabled=(os.environ.get('COURSE_TRANSCRIPT_V2_PAID_API_ENABLED','').lower()=='true' and paid['state']=='running')
    delivery={'state':'UNVERIFIED','updated_at':None}
    try:
        value=json.loads((database.parent.parent/'runtime/golden-v2-delivery.heartbeat.json').read_text())
        stamp=datetime.fromisoformat(value['updated_at'])
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        if value.get('service')=='golden-v2-delivery' and stamp.tzinfo is not None and 0<=age<=90:
            delivery={'state':value['state'],'updated_at':value['updated_at']}
        else: delivery={'state':'STALE_OR_STOPPED','updated_at':value.get('updated_at')}
    except (OSError,ValueError,KeyError,TypeError):
        pass
    publish_enabled=(os.environ.get('COURSE_TRANSCRIPT_V2_PUBLISH_API_ENABLED','').lower()=='true' and delivery['state']=='running')
    return {
        "schema": "golden-v2-chat-service-v1", "release_sha": _revision(),
        "capabilities": {"drive_link_preview": True, "batch_source_intake": True,
                         "job_and_batch_status": True, "paid_execution": paid_enabled,
                         "trusted_duration_quote": True,
                         "drive_publication": publish_enabled},
        "intake_worker": worker, "cost_estimates": "SERVER_DURATION_PROVIDER_RESERVATION",
        "paid_worker":paid,
        "delivery_worker":delivery,
        "publication_policy":"CREATE_NEW_SRT_TXT_ONLY_EXISTING_FILES_REQUIRE_SEPARATE_REVISION_POLICY",
        "golden_acceptance": "REQUIRED_SEPARATELY",
    }


@app.get('/api/v1/golden-v2/drive-health')
def drive_connection_status(authorization: str | None = Header(default=None)) -> dict:
    _authorized_database(authorization)
    try:
        return drive_health()
    except DriveApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
