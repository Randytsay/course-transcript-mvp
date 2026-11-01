"""Independent AUTO_ACCEPTED_WITH_REVIEW ledger, never Golden PASS.

Caller must run an authenticated Drive publication workflow with source version
check and full byte readback before calling finalize; no legacy status changes.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_auto_acceptance import SCHEMA, TIER, evaluate_auto_acceptance


def _wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS v2_auto_acceptance_deliveries (
        job_id TEXT PRIMARY KEY REFERENCES v2_jobs(id),
        source_sha TEXT NOT NULL,
        source_revision INTEGER NOT NULL,
        manifest_sha TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PREPARED','AUTO_DELIVERED')),
        remote_receipt_json TEXT,
        prepared_at TEXT NOT NULL,
        delivered_at TEXT
    )''')


def prepare_auto_acceptance(*, root: Path, job_id: str, result: dict,
                            srt_path: Path, txt_path: Path,
                            target_names: tuple[str, str]) -> dict:
    """Prepare isolated closure without mutating Golden provider-stage rows."""
    if (not re.fullmatch(r'v2p_[0-9a-f]{32}', job_id)
            or not isinstance(result, dict)
            or result.get('schema') != SCHEMA or result.get('status') != TIER
            or result.get('formal_golden_pass') is not False
            or result.get('can_close_without_human') is not True
            or result.get('technical_blockers') != []):
        raise V2Conflict('auto acceptance failed technical gate')
    root = Path(root)
    ledger = GoldenV2Ledger.open_existing(root / 'ledger.sqlite')
    state = ledger.status(job_id)
    if state['source_sha'] != result['source_sha256'] or not result.get('source_audio_sha_verified'):
        raise V2Conflict('acceptance/source identity mismatch')
    if (not isinstance(target_names, tuple) or len(target_names) != 2
            or not target_names[0].endswith('_自動驗收_附複核建議.srt')
            or target_names[1] != target_names[0][:-4]+'.txt'
            or any('/' in x or '\\' in x or len(x)>180 for x in target_names)):
        raise V2Conflict('auto-acceptance destination names invalid')
    if state['status'] not in {'NEEDS_REVIEW','PROCESSING','AWAITING_FINAL_REVIEW'}:
        raise V2Conflict('wrong Golden job lifecycle state')
    job_dir=(root/'jobs'/job_id).resolve()
    hashes={}
    for key,p in (('srt_sha256',srt_path),('txt_sha256',txt_path)):
        path=Path(p)
        if (path.is_symlink() or not path.is_file() or
                not path.resolve().is_relative_to(job_dir) or path.stat().st_size>20_000_000):
            raise V2Conflict('unverified or external output file')
        raw=path.read_bytes()
        if _sha(raw)!=result['artifact_shas'][key]:
            raise V2Conflict('auto acceptance bytes changed')
        hashes[key]=_sha(raw)
    # Do not trust a caller-provided 'source verified' boolean alone. Hash
    # the actual immutable multi-hour normalized source in bounded chunks.
    audio=job_dir/'normalized.flac'
    if audio.is_symlink() or not audio.is_file():
        raise V2Conflict('missing normalized source audio')
    checksum=hashlib.sha256()
    with audio.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            checksum.update(chunk)
    if checksum.hexdigest()!=state['source_sha']:
        raise V2Conflict('live normalized source audio SHA mismatch')
    try:
        technical=evaluate_auto_acceptance(
            job_id=job_id,source_sha256=state['source_sha'],
            source_audio_sha_verified=True,
            source_duration_ms=result['source_duration_ms'],
            srt_bytes=Path(srt_path).read_bytes(),
            txt_bytes=Path(txt_path).read_bytes(),
            reference_txt_bytes=None,original_srt_bytes=None,
            acoustic_review_items=None,chirp_stage='UNCONFIRMED')
    except (KeyError, ValueError, TypeError) as exc:
        raise V2Conflict('incomplete automatic QA input') from exc
    if technical['status']!=TIER:
        raise V2Conflict('machine-checked acceptance not reproducible')
    preflight_path=job_dir/'source-preflight.json'
    if preflight_path.is_symlink():
        raise V2Conflict('unsafe source evidence')
    preflight=json.loads(preflight_path.read_text(encoding='utf-8'))
    if preflight.get('source_sha256') != state['source_sha']:
        raise V2Conflict('source preflight mismatch')
    observation=preflight.get('drive_observation') or {}
    if (not str(observation.get('file_id') or '')
            or not str(observation.get('version') or '')):
        raise V2Conflict('missing original Drive identity')
    canonical={
        'schema':'golden-v2-auto-delivery-manifest-v1','quality_tier':TIER,
        'job_id':job_id,'source_sha256':state['source_sha'],
        'golden_revision':state['revision'],'golden_status_unchanged':state['status'],
        'source_drive_id':observation['file_id'],
        'source_drive_version':str(observation['version']),
        'acceptance_sha256':_sha(_wire(result)),
        'output_names':list(target_names),**hashes,
        'human_listening_attested':False,'golden_qa_pass':False,
        'source_srt_name':Path(srt_path).name,
        'source_txt_name':Path(txt_path).name,
    }
    digest=_sha(_wire(canonical));manifest={**canonical,'manifest_sha256':digest}
    with ledger._tx() as db:
        _schema(db)
        locked=db.execute('SELECT revision,source_sha,status FROM v2_jobs WHERE id=?',(job_id,)).fetchone()
        if (locked is None or locked['revision']!=state['revision'] or
                locked['source_sha']!=state['source_sha'] or locked['status']!=state['status']):
            raise V2Conflict('job changed during preparation')
        old=db.execute('SELECT manifest_sha,state FROM v2_auto_acceptance_deliveries WHERE job_id=?',(job_id,)).fetchone()
        if old and old['manifest_sha']!=digest:
            raise V2Conflict('different auto delivery already prepared; reconcile first')
        if not old:
            db.execute('INSERT INTO v2_auto_acceptance_deliveries VALUES (?,?,?,?,?,\'PREPARED\',NULL,?,NULL)',
                       (job_id,state['source_sha'],state['revision'],digest,_wire(manifest).decode(),_now()))
        status=old['state'] if old else 'PREPARED'
    return {'state':status,'manifest_sha256':digest,'manifest':manifest,
            'original_golden_state_unchanged':True}


def finalize_auto_acceptance(*, root: Path, manifest: dict,
                             remote_files: list[dict],
                             owner_grant: dict, grant_verify,
                             source_version_verify, readback_verify) -> dict:
    """Close on exact remote readback; never rewrites original Golden status.

    The trusted caller must verify source Drive version/MD5 under the same
    Drive lock and pass a scoped owner-authorized publication result.
    """
    if (not isinstance(owner_grant,dict) or not callable(grant_verify)
            or not callable(source_version_verify) or not callable(readback_verify)):
        raise V2Conflict('owner-authenticated publication required')
    root=Path(root);job_id=manifest['job_id']
    canonical=dict(manifest);digest=canonical.pop('manifest_sha256')
    if (not re.fullmatch(r'[0-9a-f]{64}',str(digest)) or
            digest!=_sha(_wire(canonical)) or
            canonical.get('schema')!='golden-v2-auto-delivery-manifest-v1' or
            canonical.get('quality_tier')!=TIER or
            canonical.get('human_listening_attested') is not False or
            canonical.get('golden_qa_pass') is not False):
        raise V2Conflict('forged auto delivery manifest')
    required={
        'job_id':job_id,'manifest_sha256':digest,
        'source_sha256':manifest['source_sha256'],
        'quality_tier':TIER,'output_names':manifest['output_names'],
        'approve_auto_delivery':True,
    }
    if (any(owner_grant.get(k)!=v for k,v in required.items())
            or not str(owner_grant.get('actor') or '').strip()
            or not str(owner_grant.get('reference') or '').strip()
            or grant_verify(owner_grant) is not True
            or source_version_verify(manifest['source_drive_id'],
                                     manifest['source_drive_version']) is not True):
        raise V2Conflict('source-version-bound signed owner delivery grant invalid')
    expected=dict(zip(manifest['output_names'],[manifest['srt_sha256'],manifest['txt_sha256']]))
    if (not isinstance(remote_files,list) or len(remote_files)!=2 or
            set(x.get('name') for x in remote_files if isinstance(x,dict))!=set(expected)):
        raise V2Conflict('wrong Drive output inventory')
    for item in remote_files:
        if not readback_verify(item['name'],item.get('file_id'),expected[item['name']]):
            raise V2Conflict('Drive SHA or source parent mismatch')
    ledger=GoldenV2Ledger.open_existing(root/'ledger.sqlite')
    with ledger._tx() as db:
        _schema(db)
        row=db.execute('SELECT * FROM v2_auto_acceptance_deliveries WHERE job_id=?',(job_id,)).fetchone()
        job=db.execute('SELECT * FROM v2_jobs WHERE id=?',(job_id,)).fetchone()
        if (row is None or row['manifest_sha']!=digest or
                row['manifest_json']!=_wire(manifest).decode() or
                job is None or job['revision']!=manifest['golden_revision'] or
                job['source_sha']!=manifest['source_sha256'] or
                job['status']!=manifest['golden_status_unchanged']):
            raise V2Conflict('auto-delivery state changed')
        receipt={'schema':'golden-v2-auto-delivery-receipt-v1',
                 'quality_tier':TIER,'state':'AUTO_DELIVERED',
                 'job_id':job_id,'manifest_sha256':digest,
                 'remote_files':remote_files,'readback_verified':True,
                 'owner_grant_sha256':_sha(_wire(owner_grant)),
                 'golden_job_status_unchanged':job['status'],
                 'golden_qa_pass':False,'human_listening_attested':False}
        wire=_wire(receipt).decode()
        if row['state']=='AUTO_DELIVERED' and row['remote_receipt_json']!=wire:
            raise V2Conflict('non-idempotent completed receipt')
        if row['state']=='PREPARED':
            db.execute('UPDATE v2_auto_acceptance_deliveries SET state=\'AUTO_DELIVERED\',remote_receipt_json=?, delivered_at=? WHERE job_id=? AND state=\'PREPARED\'',(wire,_now(),job_id))
    return receipt
