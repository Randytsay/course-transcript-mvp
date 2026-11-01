"""Disabled-by-default owner API for independent nonhuman delivery.

Human listening is optional; the owner still authorizes Drive publication.
No endpoint submits ASR, changes prior paid/GOLDEN stages, or uses V1 DB.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.golden_v2_routes import _authorized_database, _authorized_write_database
from app.golden_v2_review_routes import job_directory
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_auto_closeout import prepare_auto_acceptance, _wire
from app.pipeline.golden_v2_auto_publication import (publish_signed_auto_acceptance,
                                                       read_auto_proposal)
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_auto_acceptance import (evaluate_auto_acceptance,
                                                       render_human_review_advisory,TIER)
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_auto_gap_triage import load_bound_triage
from app.providers.golden_v2_auto_context_triage import read_source_bound_comparison

router=APIRouter(prefix='/api/v1/golden-v2')


def _enabled():
    if os.environ.get('COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED','').lower()!='true':
        raise HTTPException(status_code=404,detail='auto delivery disabled')


def _safe_selected(folder: Path, filename: str) -> Path:
    if (not filename or Path(filename).name!=filename or filename in {'.','..'}
            or '/' in filename or '\\' in filename or len(filename)>180):
        raise V2Conflict('unsafe selected filename')
    path=folder/filename
    if path.is_symlink() or not path.is_file() or path.stat().st_size>20_000_000:
        raise V2Conflict('selected source subtitle missing or unsafe')
    return path


class AutoPrepare(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: StrictInt=Field(ge=0)
    source_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    srt_filename: str=Field(min_length=5,max_length=180)
    txt_filename: str=Field(min_length=5,max_length=180)
    lesson_title: str=Field(min_length=1,max_length=160)
    original_srt_filename: str | None=Field(default=None,max_length=180)
    reference_txt_filename: str | None=Field(default=None,max_length=180)
    confirm: Literal[True]


class AutoAuthorize(BaseModel):
    model_config=ConfigDict(extra='forbid')
    manifest_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    revision: StrictInt=Field(ge=0)
    review_reference: str=Field(min_length=1,max_length=256)
    confirm: Literal[True]


class AutoPublish(BaseModel):
    model_config=ConfigDict(extra='forbid')
    manifest_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    confirm: Literal[True]


@router.get('/jobs/{job_id}/auto-delivery/status')
def auto_status(job_id: str, authorization: str | None=Header(default=None)):
    database=_authorized_database(authorization);job_directory(database,job_id)
    try:
        with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_auto_acceptance_deliveries'").fetchone()
            if not exists:
                return {'job_id':job_id,'quality_tier':TIER,'deliveries':[],'golden_pass':False}
            rows=db.execute('SELECT state,manifest_sha,prepared_at,delivered_at '
                            'FROM v2_auto_acceptance_deliveries WHERE job_id=?',(job_id,)).fetchall()
        return {'job_id':job_id,'quality_tier':TIER,'deliveries':[dict(r) for r in rows],
                'golden_pass':False}
    except (OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=503,detail='auto ledger unavailable') from exc


@router.post('/jobs/{job_id}/auto-delivery/prepare')
def prepare_auto(job_id: str, body: AutoPrepare,authorization: str | None=Header(default=None)):
    database,actor,_=_authorized_write_database(authorization);_enabled()
    job=job_directory(database,job_id)
    try:
        state=GoldenV2Ledger.open_existing(database).status(job_id)
        if state['revision']!=body.revision or state['source_sha']!=body.source_sha256:
            raise V2Conflict('stale job revision or source')
        folder=job/'golden-v2'
        srt=_safe_selected(folder,body.srt_filename)
        txt=_safe_selected(folder,body.txt_filename)
        if srt==txt: raise V2Conflict('same source for SRT and TXT')
        original=_safe_selected(folder,body.original_srt_filename) if body.original_srt_filename else None
        reference=_safe_selected(folder,body.reference_txt_filename) if body.reference_txt_filename else None
        source=job/'normalized.flac'
        if source.is_symlink() or not source.is_file():
            raise V2Conflict('source missing')
        duration=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                       '-of','default=noprint_wrappers=1:nokey=1',str(source)],
                       check=True,capture_output=True,text=True,timeout=30)
        milliseconds=round(float(duration.stdout.strip())*1000)
        chirp_stage=next(s['state'] for s in state['stages'] if s['name']=='chirp_independent')
        # Use optional bounded provider-free acoustic sidecar only when it is
        # exact source/SRT/timebase-bound. Missing sidecar stays NOT_RUN.
        triage=load_bound_triage(job_dir=job,source_sha256=state['source_sha'],
            srt_bytes=srt.read_bytes(),duration_ms=milliseconds)
        result=evaluate_auto_acceptance(job_id=job_id,source_sha256=state['source_sha'],
               source_audio_sha_verified=True,source_duration_ms=milliseconds,
               srt_bytes=srt.read_bytes(),txt_bytes=txt.read_bytes(),
               reference_txt_bytes=reference.read_bytes() if reference else None,
               original_srt_bytes=original.read_bytes() if original else None,
               acoustic_review_items=(triage['gaps'] if triage else None),chirp_stage=chirp_stage,
               external_provider_reconciled=False,source_revision_verified=False)
        context=read_source_bound_comparison(job_dir=job,job_id=job_id,
             source_sha256=state['source_sha'],stages=state['stages'])
        if context:
            result.update(context)
            if context['semantic_review_items']:
                result['human_review_advisory']['recommended']=True
        else:
            result['semantic_status']='NOT_RUN_OR_NOT_ATTESTED'
            result['semantic_review_items']=[]
        if triage:
            from app.pipeline.golden_v2_auto_closeout import _sha as auto_sha
            result['acoustic_triage_sha256']=auto_sha(_wire(triage))
        if result['status']!=TIER:
            return {'job_id':job_id,'status':result['status'],
                    'technical_blockers':result['technical_blockers'],
                    'publication_granted':False,'golden_pass':False}
        # Create-only report sidecars in the actual job folder; under a source-
        # and input-content-specific identifier to prevent edited data reuse.
        from app.pipeline.golden_v2_auto_closeout import _sha
        seed=_sha(_wire(result))[:20]
        json_name=f'auto-qa-{seed}.json'
        md_name=f'auto-review-{seed}.md'
        store_immutable_bytes(folder/json_name,_wire(result))
        store_immutable_bytes(folder/md_name,
            render_human_review_advisory(result,lesson_title=body.lesson_title).encode('utf-8'))
        stem=Path(body.srt_filename).stem
        if stem.endswith('_自動驗收_附複核建議'):
            stem=stem[:-len('_自動驗收_附複核建議')]
        if len(stem)>120: raise V2Conflict('too long output basename')
        names=(stem+'_自動驗收_附複核建議.srt',stem+'_自動驗收_附複核建議.txt')
        reports=(stem+'_自動驗收_驗收.json',stem+'_自動驗收_人工建議複核.md')
        prepared=prepare_auto_acceptance(root=database.parent,job_id=job_id,
            result=result,srt_path=srt,txt_path=txt,target_names=names,
            report_json_path=folder/json_name,report_md_path=folder/md_name,
            report_names=reports,lesson_title=body.lesson_title)
        return {'job_id':job_id,'state':prepared['state'],
                'manifest_sha256':prepared['manifest_sha256'],
                'output_names':list(names)+list(reports),
                'acoustic_unknown_count':result['acoustic_open_item_count'] if triage else result['subtitle_gaps_over_3s_count'],
                'human_listening_required':False,'golden_pass':False,
                'semantic_review_items':len(result.get('semantic_review_items',[])),
                'publication_granted':False,'actor':actor}
    except (V2Conflict,ValueError,KeyError,RuntimeError,OSError,subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=409,detail='auto source or artifact verification blocked') from exc


@router.post('/jobs/{job_id}/auto-delivery/authorize-publication')
def authorize_auto(job_id: str,body: AutoAuthorize,authorization: str | None=Header(default=None)):
    database,actor,_=_authorized_write_database(authorization);_enabled()
    job=job_directory(database,job_id)
    try:
        manifest=read_auto_proposal(database.parent,job_id,body.manifest_sha256)
        state=GoldenV2Ledger.open_existing(database).status(job_id)
        if state['revision']!=body.revision or body.revision!=manifest['golden_revision']:
            raise V2Conflict('stale revision; reprepare output')
        preflight=json.loads((job/'source-preflight.json').read_bytes())
        obs=preflight['drive_observation']
        live=DriveDeliveryGateway().get(manifest['source_drive_id'])
        if (not live or live.get('trashed') or live.get('id')!=obs['file_id']
                or int(live.get('size',-1))!=int(obs['size_bytes'])
                or live.get('md5Checksum')!=obs.get('downloaded_md5',
                                  preflight.get('original_md5_checksum'))
                or len(live.get('parents',[]))!=1
                or not str(live.get('version','')).isdigit()
                or int(live['version'])<int(manifest['source_drive_version'])):
            raise V2Conflict('Drive source changed')
        grant={'job_id':job_id,'manifest_sha256':body.manifest_sha256,
              'source_sha256':manifest['source_sha256'],
              'quality_tier':TIER,'output_names':manifest['output_names'],
              'approve_auto_delivery':True,'actor':actor,
              'reference':body.review_reference,
              'source_version_at_approval':str(live['version']),
              'human_listening_attested':False}
        target=job/'owner-auto-delivery-grants'/(body.manifest_sha256+'.json')
        store_immutable_bytes(target,_wire(signed_grant(grant)))
        return {'job_id':job_id,'state':'AUTO_PUBLICATION_AUTHORIZED',
                'manifest_sha256':body.manifest_sha256,'golden_pass':False}
    except (OSError,KeyError,ValueError,V2Conflict,RuntimeError) as exc:
        raise HTTPException(status_code=409,detail='auto owner approval/source evidence conflict') from exc


@router.post('/jobs/{job_id}/auto-delivery/publish')
def publish_auto(job_id: str,body: AutoPublish,authorization: str | None=Header(default=None)):
    database,_,_=_authorized_write_database(authorization);_enabled()
    job_directory(database,job_id)
    try:
        return publish_signed_auto_acceptance(root=database.parent,job_id=job_id,
            manifest_sha=body.manifest_sha256,lock_root=database.parent)
    except (OSError,KeyError,ValueError,V2Conflict,RuntimeError) as exc:
        raise HTTPException(status_code=409,detail='auto Drive publication not safely completed') from exc
