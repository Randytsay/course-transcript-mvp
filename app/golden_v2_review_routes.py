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
from app.pipeline.golden_v2_delivery import manifest_digest
from app.pipeline.golden_v2_ai_delivery import (
    attest_ai_review, checked_ai_outputs, authorize_ai_publication,
    prepare_ai_quality_report,
)
from app.providers.golden_v2_versioned_artifacts import resolve_versioned
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.pipeline.golden_v2_quote_intake import wire
import os
from scripts.golden_v2_review_overlaps import inspect_pending_overlaps
from app.pipeline.golden_v2_primary_stage import _primary_audit_path
from app.providers.gemini_primary_audio import PROMPT_VERSION

router=APIRouter(prefix='/api/v1/golden-v2')


def _primary_hold_diagnostic(job: Path, job_id: str, source_sha: str,
                             evidence_sha: str) -> dict[str, Any]:
    """Explain a known provider hold without returning raw audio or provider text.

    This read-only path reports specific response issues only when the ledger,
    immutable stage report and per-window raw audit all agree. Unverifiable
    records remain an integrity hold, never an invitation to retry a model.
    """
    path = job/'golden-v2'/'primary-stage.json'
    try:
        if not path.is_file() or path.is_symlink():
            return {'diagnostic_code': 'PRIMARY_REPORT_MISSING_OR_CHANGED'}
        if path.stat().st_size > 16 * 1024 * 1024:
            return {'diagnostic_code': 'PRIMARY_REPORT_UNREADABLE'}
        if file_sha256(path) != evidence_sha:
            return {'diagnostic_code': 'PRIMARY_REPORT_MISSING_OR_CHANGED'}
        report = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(report, dict) or
                report.get('schema') != 'golden-v2-primary-stage-v1' or
                report.get('job_id') != job_id or
                report.get('source_sha256') != source_sha or
                report.get('mode') != 'gemini_3_8_audio_first' or
                report.get('golden_approved') is not False or
                report.get('ready_for_chirp') is not False):
            return {'diagnostic_code': 'PRIMARY_REPORT_BINDING_CONFLICT'}
        windows = report.get('windows')
        evidence = report.get('window_evidence')
        if (not isinstance(windows, list) or not windows or
                not isinstance(evidence, list) or not evidence or
                len(evidence) > len(windows)):
            return {'diagnostic_code': 'PRIMARY_WINDOW_EVIDENCE_UNAVAILABLE'}
        for index, item in enumerate(evidence):
            if not isinstance(item, dict) or index >= len(windows):
                return {'diagnostic_code': 'PRIMARY_WINDOW_EVIDENCE_UNAVAILABLE'}
            identity = item.get('identity')
            audit_sha = item.get('raw_audit_sha256')
            window = windows[index]
            if not isinstance(window, dict):
                return {'diagnostic_code': 'PRIMARY_WINDOW_EVIDENCE_UNAVAILABLE'}
            bound_window = {key: window.get(key) for key in (
                'index', 'start_seconds', 'end_seconds', 'gcs_uri', 'mime_type')}
            if (not isinstance(identity, dict) or
                    identity.get('model') != 'gemini-3.8-flash' or
                    identity.get('prompt_version') != PROMPT_VERSION or
                    identity.get('source_sha256') != source_sha or
                    not isinstance(identity.get('context_sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', identity['context_sha256']) or
                    identity.get('window') != bound_window or
                    not isinstance(audit_sha, str) or
                    not re.fullmatch(r'[0-9a-f]{64}', audit_sha)):
                return {'diagnostic_code': 'PRIMARY_WINDOW_BINDING_CONFLICT'}
            audit_path = _primary_audit_path(job, identity)
            if audit_path.is_symlink() or not audit_path.is_file():
                return {'diagnostic_code': 'PRIMARY_RAW_AUDIT_MISSING_OR_CHANGED'}
            if audit_path.stat().st_size > 16 * 1024 * 1024:
                return {'diagnostic_code': 'PRIMARY_RAW_AUDIT_UNREADABLE'}
            if file_sha256(audit_path) != audit_sha:
                return {'diagnostic_code': 'PRIMARY_RAW_AUDIT_MISSING_OR_CHANGED'}
            raw = json.loads(audit_path.read_text(encoding='utf-8'))
            if (not isinstance(raw, dict) or raw.get('identity') != identity or
                    raw.get('finish_reason') != item.get('finish_reason') or
                    raw.get('usage_metadata') != item.get('usage_metadata')):
                return {'diagnostic_code': 'PRIMARY_RAW_AUDIT_BINDING_CONFLICT'}
            if raw.get('status') == 'REQUIRES_REVIEW':
                if raw.get('human_review_required') is not True:
                    return {'diagnostic_code': 'PRIMARY_RAW_AUDIT_STATE_CONFLICT'}
                if not str(raw.get('text') or '').strip():
                    code = 'GEMINI_EMPTY_RESPONSE'
                elif raw.get('finish_reason') not in {'STOP', 'FINISH_REASON_STOP'}:
                    code = 'GEMINI_NON_STOP_FINISH'
                else:
                    code = 'GEMINI_RESPONSE_REVIEW_REQUIRED'
                return {'diagnostic_code': code, 'window_index': index}
            if raw.get('status') != 'COMPLETE':
                return {'diagnostic_code': 'PRIMARY_RAW_AUDIT_STATE_CONFLICT'}
        return {'diagnostic_code': 'PRIMARY_OVERLAP_EVIDENCE_CONFLICT'}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return {'diagnostic_code': 'PRIMARY_REVIEW_EVIDENCE_UNREADABLE'}


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
                    'evidence_sha256':first['evidence_sha'],'human_listening_required':True,
                    **_primary_hold_diagnostic(job,job_id,state['source_sha'],first['evidence_sha'])}
    directory=job/'golden-v2'
    reports={}
    for name,stage_name in [
        ('engine-comparison.json','compare'),('golden-qa.json','golden_qa')
    ]:
        current=next((s for s in state['stages'] if s['name']==stage_name),None)
        if current is None or current['state'] not in {'COMPLETE','REVIEW_REQUIRED'}:
            continue
        digest=current.get('evidence_sha')
        if digest is None:
            continue
        try:
            path=resolve_versioned(directory/name,digest)
        except V2Conflict as exc:
            raise HTTPException(status_code=409,detail='Ledger review evidence conflict') from exc
        if path.stat().st_size>2*1024**2:
            raise HTTPException(status_code=409,detail='Oversize review evidence')
        reports[name]={'sha256':digest,'report':json.loads(path.read_text())}
    current_qa=reports.get('golden-qa.json',{}).get('report',{})
    acoustic_sha=current_qa.get('acoustic_sha256')
    if acoustic_sha:
        try:
            path=resolve_versioned(directory/'acoustic-qa.json',acoustic_sha)
        except V2Conflict as exc:
            raise HTTPException(status_code=409,detail='Acoustic review evidence conflict') from exc
        if path.stat().st_size>2*1024**2:
            raise HTTPException(status_code=409,detail='Oversize acoustic evidence')
        reports['acoustic-qa.json']={'sha256':acoustic_sha,
                                    'report':json.loads(path.read_text())}
    elif (directory/'acoustic-qa.json').is_file():
        # Legacy QA did not persist its acoustic artifact SHA. Do not
        # represent this historical report as bound to the current QA.
        path=directory/'acoustic-qa.json'
        if path.is_symlink(): raise HTTPException(status_code=409,detail='Review evidence conflict')
        if path.stat().st_size<=2*1024**2:
            reports['acoustic-qa.json']={'sha256':file_sha256(path),
                'report':json.loads(path.read_text()),'legacy_unbound':True}
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
    # Signed exact old file revisions to rename as (舊) before promotion.
    # Empty by default; unapproved old targets block publication.
    existing_files: dict[str, dict[str, str]]=Field(default_factory=dict,max_length=2)
    review_reference: str=Field(min_length=1,max_length=256)
    confirm_listened: Literal[True]
    confirm: Literal[True]


@router.post('/jobs/{job_id}/authorize-publication')
def authorize_publication(job_id: str, body: PublicationReview, authorization: str | None=Header(default=None)):
    database,actor,_=_authorized_write_database(authorization); job=job_directory(database,job_id)
    if os.environ.get('COURSE_TRANSCRIPT_V2_PUBLISH_API_ENABLED','').lower()!='true':
        raise HTTPException(status_code=404,detail='Publication not enabled')
    try:
        state,_,_,qa_sha,manifest,outputs=checked_outputs(database.parent,job_id)
        if (state['revision']!=body.revision or state['source_sha']!=body.source_sha256 or qa_sha!=body.qa_sha256 or
            manifest_digest(manifest)!=body.render_sha256 or list(outputs)!=body.filenames):
            raise V2Conflict('final publication/source/revision review changed')
        grant={**body.model_dump(exclude={'confirm'}),'job_id':job_id,'actor':actor}
        store_immutable_bytes(job/'owner-publish-grant.json',wire(signed_grant(grant)).encode())
        return {'job_id':job_id,'status':'PUBLICATION_AUTHORIZED','filenames':body.filenames}
    except (ValueError,RuntimeError,OSError,KeyError) as exc:
        raise HTTPException(status_code=409,detail='Final reviewed QA and immutable delivery hashes required') from exc



class AIPrepareReview(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revision: StrictInt=Field(ge=0)
    confirm: Literal[True]


@router.post("/jobs/{job_id}/prepare-ai-review")
def prepare_machine_review(
    job_id: str, body: AIPrepareReview,
    authorization: str | None=Header(default=None),
):
    database, actor, _ = _authorized_write_database(authorization)
    job_directory(database, job_id)
    if os.environ.get("COURSE_TRANSCRIPT_V2_AI_PUBLISH_API_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=404,detail="AI review not enabled")
    try:
        state=GoldenV2Ledger.open_existing(database).status(job_id)
        if state["revision"] != body.revision or not actor.strip():
            raise V2Conflict("review revision changed")
        prepared=prepare_ai_quality_report(database.parent,job_id)
        attested=attest_ai_review(database.parent,job_id)
        return {**prepared, **attested}
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        raise HTTPException(status_code=409, detail="AI machine QA not yet eligible") from exc

class AIReviewAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=0)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]


@router.post("/jobs/{job_id}/attest-ai-review")
def attest_machine_review(job_id: str, body: AIReviewAttestation,
                          authorization: str | None = Header(default=None)):
    database, actor, _ = _authorized_write_database(authorization)
    job = job_directory(database, job_id)
    if os.environ.get("COURSE_TRANSCRIPT_V2_AI_PUBLISH_API_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=404, detail="AI review is not enabled")
    try:
        state, _, _, cert, _ = checked_ai_outputs(
            database.parent, job_id, require_attestation=False)
        if (body.revision != state["revision"] or
                body.report_sha256 != cert["report_sha256"] or not actor.strip()):
            raise V2Conflict("AI report changed since owner review")
        return attest_ai_review(database.parent, job_id)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="AI QA evidence not eligible") from exc


class AIPublicationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=0)
    certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filenames: list[str] = Field(min_length=2, max_length=2)
    review_reference: str = Field(min_length=1, max_length=256)
    confirm: Literal[True]


@router.post("/jobs/{job_id}/authorize-ai-publication")
def authorize_machine_publication(
    job_id: str, body: AIPublicationReview,
    authorization: str | None = Header(default=None),
):
    database, actor, _ = _authorized_write_database(authorization)
    job = job_directory(database, job_id)
    if os.environ.get("COURSE_TRANSCRIPT_V2_AI_PUBLISH_API_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=404, detail="AI publication not enabled")
    try:
        return authorize_ai_publication(
            root=database.parent, job_id=job_id, revision=body.revision,
            certificate_sha256=body.certificate_sha256, filenames=body.filenames,
            actor=actor, review_reference=body.review_reference)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="AI publication source/QA/grant conflict") from exc
