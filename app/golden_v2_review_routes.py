"""Authenticated, provider-free review entry; never invent listening receipts."""
import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.golden_v2_routes import _authorized_database, _authorized_write_database
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_primary_stage import finalize_reviewed_primary_overlaps
from app.pipeline.golden_v2_stage_runner import complete_adjudication_or_hold
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_delivery import checked_outputs
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.pipeline.golden_v2_quote_intake import wire
import os
from scripts.golden_v2_review_overlaps import inspect_pending_overlaps

router=APIRouter(prefix='/api/v1/golden-v2')


def job_directory(database, job_id):
    if not re.fullmatch(r'v2p_[0-9a-f]{32}',job_id):
        raise HTTPException(status_code=422,detail='Invalid job ID')
    root=database.parent/'jobs'; job=root/job_id
    if root.is_symlink() or job.is_symlink() or not job.is_dir():
        raise HTTPException(status_code=404,detail='Source artifacts unavailable')
    return job


@router.get('/jobs/{job_id}/review')
def review_packet(job_id: str, authorization: str | None = Header(default=None)):
    database=_authorized_database(authorization); job=job_directory(database,job_id)
    ledger=GoldenV2Ledger.open_existing(database); state=ledger.status(job_id)
    first=state['stages'][0]
    if first['state']=='REVIEW_REQUIRED':
        try:
            packet=inspect_pending_overlaps(database=database,job_id=job_id,job_dir=job,normalized_audio=job/'normalized.flac')
            packet.pop('normalized_audio',None)
            return {'kind':'PRIMARY_OVERLAP_REVIEW','packet':packet,'human_listening_required':True,
                    'reviewer_identity':os.environ.get('COURSE_TRANSCRIPT_V2_OWNER_ACTOR','')}
        except (ValueError,RuntimeError,OSError,KeyError):
            return {'kind':'PROVIDER_RESPONSE_REVIEW','job_id':job_id,'revision':state['revision'],
                    'evidence_sha256':first['evidence_sha'],'human_listening_required':True}
    directory=job/'golden-v2'
    reports={}
    for name in ['engine-comparison.json','acoustic-qa.json','golden-qa.json']:
        path=directory/name
        if path.is_symlink(): raise HTTPException(status_code=409,detail='Review evidence conflict')
        if path.is_file() and path.stat().st_size<=2*1024**2:
            reports[name]={'sha256':file_sha256(path),'report':json.loads(path.read_text())}
    delivery=directory/'drive-delivery.json'
    if delivery.is_file() and not delivery.is_symlink():
        reports['drive-delivery.json']={'sha256':file_sha256(delivery),'report':json.loads(delivery.read_text())}
    return {'kind':'GOLDEN_EVIDENCE_REVIEW','job_id':job_id,'revision':state['revision'],
            'status':state['status'],'reports':reports,'human_listening_required':True,
            'reviewer_identity':os.environ.get('COURSE_TRANSCRIPT_V2_OWNER_ACTOR','')}


class SeamReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: StrictInt=Field(ge=0)
    evidence_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    source_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    decisions: list[dict[str,Any]]=Field(min_length=1,max_length=32)
    review_reference: str=Field(min_length=1,max_length=256)
    confirm_listened: Literal[True]
    confirm: Literal[True]


@router.post('/jobs/{job_id}/review-overlaps')
def review_overlaps(job_id: str, body: SeamReview, authorization: str | None=Header(default=None)):
    database,actor,_=_authorized_write_database(authorization)
    job=job_directory(database,job_id)
    try:
        if file_sha256(job/'normalized.flac')!=body.source_sha256:
            raise V2Conflict('listened source SHA differs from approved audio')
        return finalize_reviewed_primary_overlaps(ledger=GoldenV2Ledger.open_existing(database),job_id=job_id,
            revision=body.revision,previous_evidence_sha=body.evidence_sha256,job_dir=job,
            normalized_audio=job/'normalized.flac',seam_decisions=body.decisions,
            reviewer=actor,review_reference=body.review_reference)
    except (ValueError,RuntimeError,OSError,KeyError) as exc:
        raise HTTPException(status_code=409,detail='Review source/state/decision conflict; query review packet') from exc


class AdjudicationReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: StrictInt=Field(ge=0)
    comparison_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    source_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    decisions: list[dict[str,Any]]=Field(max_length=2000)
    replacements: list[dict[str,Any]]=Field(default_factory=list,max_length=2000)
    punctuation_after: dict[int,str]=Field(default_factory=dict)
    review_reference: str=Field(min_length=1,max_length=256)
    confirm_listened: Literal[True]
    confirm: Literal[True]


@router.post('/jobs/{job_id}/review-adjudication')
def review_adjudication(job_id: str, body: AdjudicationReview, authorization: str | None=Header(default=None)):
    database,actor,_=_authorized_write_database(authorization); job=job_directory(database,job_id)
    ledger=GoldenV2Ledger.open_existing(database)
    try:
        state=ledger.status(job_id)
        comparison=next(s for s in state['stages'] if s['name']=='compare')
        pending=next(s for s in state['stages'] if s['state'] not in {'COMPLETE','SKIPPED'})
        if (state['revision']!=body.revision or state['source_sha']!=body.source_sha256 or
            comparison['state']!='COMPLETE' or comparison['evidence_sha']!=body.comparison_sha256 or
            state['mode']!='FLASH_CHIRP' or pending['name'] not in {'third_optional','adjudication'} or
            pending['state']!='PENDING' or state['status'] not in {'PROCESSING','QUEUED'}):
            raise V2Conflict('adjudication review state changed')
        # Validate the authenticated actor on every decision before a claim.
        if any(d.get('reviewer')!=actor or d.get('review_reference')!=body.review_reference or
               d.get('source_sha256')!=body.source_sha256 for d in body.decisions):
            raise V2Conflict('review identity/source/reference mismatch')
        complete_adjudication_or_hold(ledger=ledger,claim={'job_id':job_id,'source_sha':body.source_sha256},
                 job_dir=job,decisions=body.decisions,replacements=body.replacements,
                 punctuation_after=body.punctuation_after,reviewer=actor,
                 review_reference=body.review_reference,validate_only=True)
        claim=ledger.claim_next(job_id,worker_id='authenticated-chat-review',expected_revision=body.revision)
        if claim['stage']!='adjudication': raise V2Conflict('wrong review stage')
        return complete_adjudication_or_hold(ledger=ledger,claim=claim,job_dir=job,
                 decisions=body.decisions,replacements=body.replacements,punctuation_after=body.punctuation_after,
                 reviewer=actor,review_reference=body.review_reference)
    except (ValueError,RuntimeError,OSError,KeyError) as exc:
        raise HTTPException(status_code=409,detail='Adjudication evidence rejected; retain source and query review') from exc


class PublicationReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: StrictInt=Field(ge=0)
    source_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    qa_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    render_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    filenames: list[str]=Field(min_length=2,max_length=2)
    review_reference: str=Field(min_length=1,max_length=256)
    confirm_listened: Literal[True]
    confirm: Literal[True]


@router.post('/jobs/{job_id}/authorize-publication')
def authorize_publication(job_id: str, body: PublicationReview, authorization: str | None=Header(default=None)):
    database,actor,_=_authorized_write_database(authorization); job=job_directory(database,job_id)
    if os.environ.get('COURSE_TRANSCRIPT_V2_PUBLISH_API_ENABLED','').lower()!='true':
        raise HTTPException(status_code=404,detail='Publication not enabled')
    try:
        state,_,_,qa_sha,_,outputs=checked_outputs(database.parent,job_id)
        if (state['revision']!=body.revision or state['source_sha']!=body.source_sha256 or qa_sha!=body.qa_sha256 or
            file_sha256(job/'golden-v2/render-manifest.json')!=body.render_sha256 or list(outputs)!=body.filenames):
            raise V2Conflict('final publication/source/revision review changed')
        grant={**body.model_dump(exclude={'confirm'}),'job_id':job_id,'actor':actor}
        store_immutable_bytes(job/'owner-publish-grant.json',wire(signed_grant(grant)).encode())
        return {'job_id':job_id,'status':'PUBLICATION_AUTHORIZED','filenames':body.filenames}
    except (ValueError,RuntimeError,OSError,KeyError) as exc:
        raise HTTPException(status_code=409,detail='Final reviewed QA and immutable delivery hashes required') from exc
