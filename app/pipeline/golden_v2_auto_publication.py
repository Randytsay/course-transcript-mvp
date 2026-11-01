"""Authenticated source-locked 4-file auto-acceptance publication.

Accepts a machine-grade output WITHOUT fabricated listening or Golden PASS.
Drive mutations are additive, resumable, source-bound and verified by readback.
No recognition, retry, V1 writes or changes to canonical Golden stage state.
"""
from __future__ import annotations

import hmac
import json
import re
import sqlite3
from pathlib import Path

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_auto_closeout import (_schema, _wire, _sha,
                                                   finalize_auto_acceptance)
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes


def read_auto_proposal(root: Path, job_id: str, digest: str) -> dict:
    if (not re.fullmatch(r'v2p_[0-9a-f]{32}',job_id)
            or not re.fullmatch(r'[0-9a-f]{64}',digest)):
        raise V2Conflict('invalid auto acceptance identity')
    ledger=GoldenV2Ledger.open_existing(root/'ledger.sqlite')
    with ledger._tx() as db:
        _schema(db)
        row=db.execute('SELECT manifest_json,state,manifest_sha FROM v2_auto_acceptance_deliveries '
                       'WHERE job_id=?',(job_id,)).fetchone()
    if not row or row['manifest_sha']!=digest or row['state'] not in {'PREPARED','AUTO_DELIVERED'}:
        raise V2Conflict('missing exact approved auto manifest')
    manifest=json.loads(row['manifest_json'])
    canonical=dict(manifest)
    if (canonical.pop('manifest_sha256',None)!=digest or _sha(_wire(canonical))!=digest
            or manifest.get('job_id')!=job_id or manifest.get('golden_qa_pass') is not False
            or manifest.get('human_listening_attested') is not False):
        raise V2Conflict('auto manifest content integrity failure')
    return manifest


def publish_signed_auto_acceptance(*, root: Path, job_id: str,
                                  manifest_sha: str, lock_root: Path,
                                  gateway=None) -> dict:
    root=Path(root); lock_root=Path(lock_root)
    if root.is_symlink() or lock_root.is_symlink():
        raise V2Conflict('unsafe publication roots')
    manifest=read_auto_proposal(root,job_id,manifest_sha)
    job=root/'jobs'/job_id; folder=job/'golden-v2'
    if job.is_symlink() or folder.is_symlink():
        raise V2Conflict('unsafe job folder')
    grant_path=job/'owner-auto-delivery-grants'/(manifest_sha+'.json')
    if grant_path.is_symlink() or not grant_path.is_file() or grant_path.stat().st_size>32768:
        raise V2Conflict('owner authenticated auto-delivery grant absent')
    envelope=json.loads(grant_path.read_text(encoding='utf-8'))
    grant=envelope.get('grant')
    if not isinstance(grant,dict) or not isinstance(envelope.get('signature'),str):
        raise V2Conflict('invalid signed grant')
    if not hmac.compare_digest(signed_grant(grant)['signature'],envelope['signature']):
        raise V2Conflict('invalid owner signature')
    # All four companions are required for the production owner API.
    extras=manifest.get('extra_artifacts') or []
    if len(extras)!=2:
        raise V2Conflict('auto delivery requires both QA JSON and review MD')
    files=[(manifest['output_names'][0],manifest['source_srt_name'],manifest['srt_sha256']),
           (manifest['output_names'][1],manifest['source_txt_name'],manifest['txt_sha256'])]
    files.extend((x['name'],x['filename'],x['sha256']) for x in extras)
    if len(files)!=4 or len(set(n for n,_,_ in files))!=4:
        raise V2Conflict('four distinct auto-delivery target names required')
    raws=[]
    for name,filename,digest in files:
        if (Path(filename).name!=filename or name.startswith('.') or
                '/' in name or '\\' in name or len(name)>180):
            raise V2Conflict('unsafe auto output name')
        path=folder/filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size>20_000_000:
            raise V2Conflict('auto output missing or unsafe')
        data=path.read_bytes()
        if _sha(data)!=digest:
            raise V2Conflict('auto output changed since machine QA')
        raws.append((name,data,digest))
    preflight_path=job/'source-preflight.json'
    if preflight_path.is_symlink() or not preflight_path.is_file():
        raise V2Conflict('missing original source preflight')
    preflight=json.loads(preflight_path.read_bytes())
    obs=preflight.get('drive_observation') or {}
    if (preflight.get('source_sha256')!=manifest['source_sha256']
            or obs.get('file_id')!=manifest['source_drive_id']
            or str(obs.get('version'))!=manifest['source_drive_version']):
        raise V2Conflict('original source is not preflight-bound')
    version=str(grant.get('source_version_at_approval',''))
    if (not version.isdigit() or int(version)<int(manifest['source_drive_version'])):
        raise V2Conflict('owner has not approved live source version')
    gateway=gateway or DriveDeliveryGateway()
    def check_source(file_id, source_version):
        if file_id!=manifest['source_drive_id'] or source_version!=manifest['source_drive_version']:
            return False
        obj=gateway.get(file_id)
        return bool(obj and not obj.get('trashed') and obj.get('id')==file_id
            and str(obj.get('version'))==version
            and int(obj.get('size',-1))==int(obs.get('size_bytes',-1))
            and obj.get('md5Checksum')==obs.get('downloaded_md5',preflight.get('original_md5_checksum'))
            and isinstance(obj.get('parents'),list) and len(obj['parents'])==1)
    def signed_ok(payload):
        return bool(payload==grant and
                    hmac.compare_digest(signed_grant(payload)['signature'],envelope['signature']))
    with drive_publish_lock(lock_root,preflight['drive_source_path']):
        if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
            raise V2Conflict('Drive MP3 changed before auto publication')
        parent=gateway.get(manifest['source_drive_id'])['parents'][0]
        intent_folder=folder/'auto-delivery-intents'/manifest_sha
        if intent_folder.is_symlink():
            raise V2Conflict('unsafe auto intent folder')
        intent_folder.mkdir(parents=True,exist_ok=True)
        plans=[]
        for index,(name,raw,digest) in enumerate(raws):
            intent=intent_folder/(str(index)+'.json')
            if intent.is_symlink():
                raise V2Conflict('unsafe auto intent')
            if intent.exists():
                plan=json.loads(intent.read_bytes())
                if (plan.get('sha256')!=digest or plan.get('name')!=name
                        or plan.get('parent_id')!=parent):
                    raise V2Conflict('persisted auto intent collision')
            else:
                if gateway.named(parent,name):
                    raise V2Conflict('original editor target collision; never overwrite')
                identifier=gateway.generate_id()
                plan={'file_id':identifier,'sha256':digest,'parent_id':parent,'name':name,
                      'pending_name':f'.{job_id}.auto.{manifest_sha[:16]}.{index}'}
                store_immutable_bytes(intent,_wire(plan))
            if any(row['id']!=plan['file_id'] for row in gateway.named(parent,name)):
                raise V2Conflict('other file already owns auto destination')
            plans.append((plan,raw))
        # First stage and read back ALL content, before revealing final names.
        for plan,raw in plans:
            ident=plan['file_id']
            remote=gateway.get(ident)
            if remote is None:
                if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
                    raise V2Conflict('source changed while staging outputs')
                gateway.create_pending(ident,parent,plan['pending_name'],raw,job_id)
                remote=gateway.get(ident)
            if (not remote or remote.get('trashed') or remote.get('parents')!=[parent]
                    or (remote.get('appProperties') or {}).get('golden_v2_job')!=job_id
                    or remote.get('name') not in (plan['pending_name'],plan['name'])):
                raise V2Conflict('unowned or changed pending auto file')
            observed=gateway.readback(ident)
            if not isinstance(observed,bytes):
                raise V2Conflict('Drive readback unavailable')
            if _sha(observed)!=plan['sha256']:
                if remote.get('name')!=plan['pending_name'] or observed:
                    raise V2Conflict('Drive file externally modified')
                gateway.write_pending_content(ident,raw)
                if _sha(gateway.readback(ident))!=plan['sha256']:
                    raise V2Conflict('failed to recover empty pending file')
        remote_files=[]
        for plan,raw in plans:
            if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
                raise V2Conflict('source changed before promotion')
            ident=plan['file_id'];name=plan['name']
            remote=gateway.get(ident)
            if remote.get('name')==plan['pending_name']:
                if gateway.named(parent,name):
                    raise V2Conflict('auto destination acquired during upload')
                gateway.promote(ident,name)
            if (gateway.get(ident).get('name')!=name
                    or any(f['id']!=ident for f in gateway.named(parent,name))
                    or _sha(gateway.readback(ident))!=plan['sha256']):
                raise V2Conflict('promoted output changed')
            remote_files.append({'name':name,'file_id':ident})
        def verify(name,ident,digest):
            obj=gateway.get(ident)
            return (obj is not None and not obj.get('trashed')
                    and obj.get('name')==name and obj.get('parents')==[parent]
                    and _sha(gateway.readback(ident))==digest)
        receipt=finalize_auto_acceptance(
            root=root,manifest=manifest,remote_files=remote_files,
            owner_grant=grant,grant_verify=signed_ok,
            source_version_verify=check_source,readback_verify=verify)
        receipt['public_links_created']=False
        return receipt
