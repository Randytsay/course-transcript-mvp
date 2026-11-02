"""Authenticated source-locked 4-file auto-acceptance publication.

Accepts a machine-grade output WITHOUT fabricated listening or Golden PASS.
Drive mutations in synthetic tests are additive and checked by readback.
Bound crash recovery is synthetic only; the real Drive gateway stays disabled.
No recognition, V1 writes or changes to canonical Golden stage state.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_auto_closeout import (_schema, _wire, _sha,
    _verify_grant_scope, _checked_delivery_state, finalize_auto_acceptance)
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway
from app.pipeline.golden_v2_multipart_pilot import (
    build_exact_id_multipart, verify_remote_exact, classify_http_outcome)
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes


@contextmanager
def _publication_fence(ledger, manifest):
    """Persist a fail-closed job fence before any uncertain remote operation.

    A crash or exception leaves a fence. Only a complete durable binding may
    be resumed after read-only reconciliation. Normal completion deletes it
    atomically with the receipt. SQLite triggers protect local job/stage rows;
    neither they nor the local flock protect Drive from external editors.
    """
    job_id=manifest['job_id']
    with ledger._tx() as db:
        _schema(db)
        db.execute('CREATE TABLE IF NOT EXISTS v2_auto_publication_fences '
                   '(job_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, binding_json TEXT)')
        columns={row['name'] for row in db.execute('PRAGMA table_info(v2_auto_publication_fences)')}
        if 'binding_json' not in columns:
            db.execute('ALTER TABLE v2_auto_publication_fences ADD COLUMN binding_json TEXT')
        for action, expression in (('UPDATE','job_id=OLD.id OR job_id=NEW.id'),
                                   ('DELETE','job_id=OLD.id'), ('INSERT','job_id=NEW.id')):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS v2_auto_publication_fence_{action.lower()}
                BEFORE {action} ON v2_jobs
                WHEN EXISTS (SELECT 1 FROM v2_auto_publication_fences WHERE {expression})
                BEGIN SELECT RAISE(ABORT, 'auto publication needs reconciliation'); END''')
        for action, expression in (('UPDATE','job_id=OLD.job_id OR job_id=NEW.job_id'),
                                   ('DELETE','job_id=OLD.job_id'), ('INSERT','job_id=NEW.job_id')):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS v2_auto_publication_stage_fence_{action.lower()}
                BEFORE {action} ON v2_stages
                WHEN EXISTS (SELECT 1 FROM v2_auto_publication_fences WHERE {expression})
                BEGIN SELECT RAISE(ABORT, 'auto publication needs reconciliation'); END''')
        # REPLACE implicitly deletes conflicting rows without DELETE triggers
        # when recursive_triggers is off (SQLite's default). Inspect candidate
        # rowid and declared unique keys *before* SQLite resolves conflicts.
        # Add separately named triggers so existing prototype databases upgrade
        # without dropping their original protections.
        for action in ('INSERT', 'UPDATE'):
            for table, owner, conflict in (
                ('v2_jobs', 'victim.id',
                 'victim.rowid=NEW.rowid OR victim.id=NEW.id'),
                ('v2_stages', 'victim.job_id',
                 'victim.rowid=NEW.rowid OR (victim.job_id=NEW.job_id AND victim.name=NEW.name)'),
            ):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_auto_replace_fence_{action.lower()}
                    BEFORE {action} ON {table}
                    WHEN EXISTS (SELECT 1 FROM {table} victim
                        JOIN v2_auto_publication_fences f ON f.job_id={owner}
                        WHERE {conflict})
                    BEGIN SELECT RAISE(ABORT, 'auto publication needs reconciliation'); END''')
    # Commit the protective schema separately: rejecting an old fence must not
    # roll back installation of the triggers that protect that fence.
    with ledger._tx() as db:
        delivery,_ = _checked_delivery_state(db,manifest)
        prior=db.execute('SELECT manifest_sha,binding_json FROM v2_auto_publication_fences WHERE job_id=?',(job_id,)).fetchone()
        # A completed replay has no remote writes and must not persist a new
        # fence on failure. Keep the SQLite writer lock for the whole read.
        if delivery['state']=='AUTO_DELIVERED':
            if prior:
                raise V2Conflict('completed publication has an outstanding fence')
            yield db,None
            return
        if prior:
            if prior['manifest_sha']!=manifest['manifest_sha256'] or prior['binding_json'] is None:
                raise V2Conflict('previous auto publication requires reconciliation')
            try:
                binding=json.loads(prior['binding_json'])
            except (ValueError,TypeError):
                raise V2Conflict('invalid auto publication fence binding')
            if not isinstance(binding,dict):
                raise V2Conflict('invalid auto publication fence binding')
        else:
            binding=None
            db.execute('INSERT INTO v2_auto_publication_fences (job_id,manifest_sha,binding_json) VALUES (?,?,NULL)',
                       (job_id,manifest['manifest_sha256']))
    with ledger._tx() as db:
        _checked_delivery_state(db,manifest)
        yield db,binding
        db.execute('DELETE FROM v2_auto_publication_fences WHERE job_id=? AND manifest_sha=?',
                   (job_id,manifest['manifest_sha256']))


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
    _verify_grant_scope(manifest, grant, lambda payload: hmac.compare_digest(
        signed_grant(payload)['signature'], envelope['signature']))
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
    # Production Drive's metadata create and media PATCH, and name checks and
    # promotion, are not atomic with external editors.  The local flock cannot
    # protect against them.  Keep the real gateway disabled until a protocol
    # with enforceable preconditions has been verified end to end.  Injected
    # synthetic gateways are solely for isolated tests.
    if gateway is None:
        if os.environ.get('COURSE_TRANSCRIPT_V2_ENABLE_MULTIPART_PUBLICATION') == '1':
            gateway = DriveDeliveryGateway()
        else:
            raise V2Conflict('auto publication to Drive is disabled pending concurrency-safe reconciliation')
    elif isinstance(gateway, DriveDeliveryGateway) and os.environ.get('COURSE_TRANSCRIPT_V2_ENABLE_MULTIPART_PUBLICATION') != '1':
        raise V2Conflict('auto publication to Drive is disabled pending concurrency-safe reconciliation')
    parent=None
    def check_source(file_id, source_version):
        if file_id!=manifest['source_drive_id'] or source_version!=manifest['source_drive_version']:
            return False
        obj=gateway.get(file_id)
        return bool(obj and not obj.get('trashed') and obj.get('id')==file_id
            and str(obj.get('version'))==version
            and int(obj.get('size',-1))==int(obs.get('size_bytes',-1))
            and obj.get('md5Checksum')==obs.get('downloaded_md5',preflight.get('original_md5_checksum'))
            and isinstance(obj.get('parents'),list) and len(obj['parents'])==1
            and (parent is None or obj['parents']==[parent]))
    def signed_ok(payload):
        return bool(payload==grant and
                    hmac.compare_digest(signed_grant(payload)['signature'],envelope['signature']))
    # The committed job fence survives process death; the transaction also
    # serializes receipt commit against other SQLite writers while we run.
    ledger=GoldenV2Ledger.open_existing(root/'ledger.sqlite')
    with drive_publish_lock(lock_root,preflight['drive_source_path']), _publication_fence(ledger,manifest) as (db, binding):
        if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
            raise V2Conflict('Drive MP3 changed before auto publication')
        parent=gateway.get(manifest['source_drive_id'])['parents'][0]
        if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
            raise V2Conflict('Drive MP3 changed before auto publication')
        base_binding={'manifest_sha256':manifest_sha, 'owner_grant_sha256':_sha(_wire(grant)),
                      'source_drive_id':manifest['source_drive_id'],
                      'source_version_at_approval':version, 'parent_id':parent,
                      'preflight_sha256':_sha(_wire(preflight))}
        if binding is not None and (not isinstance(binding,dict)
                or set(binding)!=set(base_binding)|{'plans'}
                or any(binding.get(k)!=v for k,v in base_binding.items())
                or not isinstance(binding.get('plans'),list) or len(binding['plans'])!=4):
            raise V2Conflict('auto publication fence binding changed')
        delivery,_job=_checked_delivery_state(db,manifest)
        completed=delivery['state']=='AUTO_DELIVERED'
        intent_folder=folder/'auto-delivery-intents'/manifest_sha
        if intent_folder.is_symlink():
            raise V2Conflict('unsafe auto intent folder')
        if completed:
            if not intent_folder.is_dir():
                raise V2Conflict('completed auto intent folder missing')
        else:
            intent_folder.mkdir(parents=True,exist_ok=True)
        # Inspect every persisted intent and every destination before allocating
        # IDs or touching Drive.  A later corrupt intent must never be discovered
        # only after an earlier file has already been created.
        existing_ids=set()
        inspected_plans=[]
        for index,(name,raw,digest) in enumerate(raws):
            intent=intent_folder/(str(index)+'.json')
            pending_name=f'.{job_id}.auto.{manifest_sha[:16]}.{index}'
            if intent.is_symlink():
                raise V2Conflict('unsafe auto intent')
            plan=None
            if binding is not None and not intent.is_file():
                raise V2Conflict('bound auto intent missing')
            if intent.exists():
                if binding is None and not completed:
                    raise V2Conflict('unbound preexisting auto intent')
                plan=json.loads(intent.read_bytes())
                if binding is not None and plan!=binding['plans'][index]:
                    raise V2Conflict('auto intent differs from durable fence')
                if (not isinstance(plan,dict) or set(plan)!={'file_id','sha256','parent_id','name','pending_name'}
                        or not isinstance(plan['file_id'],str) or not plan['file_id']
                        or plan['file_id'] in existing_ids or plan['sha256']!=digest
                        or plan['parent_id']!=parent or plan['name']!=name
                        or plan['pending_name']!=pending_name):
                    raise V2Conflict('persisted auto intent collision')
                existing_ids.add(plan['file_id'])
                remote=gateway.get(plan['file_id'])
                if remote is not None:
                    if (remote.get('id')!=plan['file_id'] or remote.get('trashed')
                            or remote.get('parents')!=[parent]
                            or (remote.get('appProperties') or {}).get('golden_v2_job')!=job_id
                            or remote.get('name') not in ((name,) if completed else (pending_name,name))):
                        raise V2Conflict('unowned or changed pending auto file')
                    observed=gateway.readback(plan['file_id'])
                    if not isinstance(observed,bytes) or _sha(observed)!=digest:
                        raise V2Conflict('Drive file content ambiguous or modified')
                elif completed or (binding is not None and (
                        (callable(getattr(gateway,'prove_never_created',None)) and getattr(gateway,'prove_never_created')(plan['file_id']) is not True)
                        or (not callable(getattr(gateway,'prove_never_created',None)) and (not hasattr(gateway,'upload_multipart') or os.environ.get('COURSE_TRANSCRIPT_V2_ENABLE_MULTIPART_PUBLICATION') != '1'))
                )):
                    raise V2Conflict('missing bound auto file has ambiguous history')
            elif completed:
                raise V2Conflict('completed auto intent missing')
            if any(plan is None or row.get('id')!=plan['file_id']
                   for row in gateway.named(parent,name)):
                raise V2Conflict('other file already owns auto destination')
            if any(plan is None or row.get('id')!=plan['file_id']
                   for row in gateway.named(parent,pending_name)):
                raise V2Conflict('other file already owns pending destination')
            inspected_plans.append(plan)
        if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
            raise V2Conflict('Drive MP3 changed during auto reconciliation')
        plans=[]
        for index,(name,raw,digest) in enumerate(raws):
            intent=intent_folder/(str(index)+'.json')
            pending_name=f'.{job_id}.auto.{manifest_sha[:16]}.{index}'
            if intent.is_symlink():
                raise V2Conflict('unsafe auto intent')
            if intent.exists():
                plan=json.loads(intent.read_bytes())
                if (plan!=inspected_plans[index] or
                        plan.get('sha256')!=digest or plan.get('name')!=name
                        or plan.get('parent_id')!=parent
                        or plan.get('pending_name')!=pending_name):
                    raise V2Conflict('persisted auto intent collision')
            else:
                if inspected_plans[index] is not None:
                    raise V2Conflict('persisted auto intent disappeared')
                if gateway.named(parent,name):
                    raise V2Conflict('original editor target collision; never overwrite')
                identifier=gateway.generate_id()
                if (not isinstance(identifier,str) or not identifier
                        or identifier in existing_ids or gateway.get(identifier) is not None):
                    raise V2Conflict('generated auto intent ID collision')
                existing_ids.add(identifier)
                plan={'file_id':identifier,'sha256':digest,'parent_id':parent,'name':name,
                      'pending_name':f'.{job_id}.auto.{manifest_sha[:16]}.{index}'}
                store_immutable_bytes(intent,_wire(plan))
            if any(row['id']!=plan['file_id'] for row in gateway.named(parent,name)):
                raise V2Conflict('other file already owns auto destination')
            plans.append((plan,raw))
        if completed:
            receipt=json.loads(delivery['remote_receipt_json'])
            if (receipt.get('owner_grant_sha256')!=base_binding['owner_grant_sha256'] or
                    receipt.get('remote_files')!=[{'name':p['name'],'file_id':p['file_id']} for p,_ in plans]):
                raise V2Conflict('completed receipt does not bind auto intents')
        recovering=binding is not None
        if binding is None and not completed:
            binding={**base_binding,'plans':[plan for plan,_ in plans]}
            db.execute('UPDATE v2_auto_publication_fences SET binding_json=? WHERE job_id=? AND binding_json IS NULL',
                       (_wire(binding).decode(),job_id))
            # The complete binding must survive before the first remote create.
            # The committed fence triggers continue protecting job/stage rows.
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            _checked_delivery_state(db,manifest)
            stored=db.execute('SELECT manifest_sha,binding_json FROM v2_auto_publication_fences WHERE job_id=?',(job_id,)).fetchone()
            if not stored or stored['manifest_sha']!=manifest_sha or stored['binding_json']!=_wire(binding).decode():
                raise V2Conflict('auto publication fence changed')
        # First stage and read back ALL content, before revealing final names.
        for plan,raw in plans:
            ident=plan['file_id']
            remote=gateway.get(ident)
            if remote is None:
                if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
                    raise V2Conflict('source changed while staging outputs')
                # Recheck at the point of use: an object inspected above may
                # have disappeared since reconciliation. A 404 is not evidence
                # of non-creation, even when an earlier GET succeeded.
                if completed or (recovering and (
                        (callable(getattr(gateway,'prove_never_created',None)) and getattr(gateway,'prove_never_created')(ident) is not True)
                        or (not callable(getattr(gateway,'prove_never_created',None)) and (not hasattr(gateway,'upload_multipart') or os.environ.get('COURSE_TRANSCRIPT_V2_ENABLE_MULTIPART_PUBLICATION') != '1'))
                )):
                    raise V2Conflict('missing bound auto file has ambiguous history')
                ext=Path(plan['name']).suffix
                req=build_exact_id_multipart(
                    file_id=ident,parent_id=parent,name=plan['pending_name'],
                    media=raw,job_id=job_id,manifest_sha256=manifest_sha,
                    expected_media_sha256=plan['sha256'],artifact_extension=ext,
                    pending=True)
                if hasattr(gateway,'upload_multipart'):
                    gateway.upload_multipart(req)
                else:
                    gateway.create_pending(ident,parent,plan['pending_name'],raw,job_id)
                remote=gateway.get(ident)
            if (not remote or remote.get('id')!=ident or remote.get('trashed') or remote.get('parents')!=[parent]
                    or (remote.get('appProperties') or {}).get('golden_v2_job')!=job_id
                    or remote.get('name') not in ((plan['name'],) if completed else (plan['pending_name'],plan['name']))):
                raise V2Conflict('unowned or changed pending auto file')
            observed=gateway.readback(ident)
            if not isinstance(observed,bytes):
                raise V2Conflict('Drive readback unavailable')
            if _sha(observed)!=plan['sha256']:
                # An empty object is not proof it is safe to overwrite: another
                # actor or an in-flight upload may own the bytes now.
                raise V2Conflict('Drive file content ambiguous or modified')
        remote_files=[]
        for plan,raw in plans:
            if not check_source(manifest['source_drive_id'],manifest['source_drive_version']):
                raise V2Conflict('source changed before promotion')
            ident=plan['file_id'];name=plan['name']
            remote=gateway.get(ident)
            if remote is None:
                raise V2Conflict('auto output disappeared before promotion')
            if remote.get('name')==plan['pending_name']:
                if completed:
                    raise V2Conflict('completed auto output name changed')
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
            content=gateway.readback(ident) if obj is not None else None
            matches=gateway.named(parent,name)
            return (obj is not None and obj.get('id')==ident and not obj.get('trashed')
                    and obj.get('name')==name and obj.get('parents')==[parent]
                    and (obj.get('appProperties') or {}).get('golden_v2_job')==job_id
                    and len(matches)==1 and matches[0].get('id')==ident
                    and isinstance(content,bytes) and _sha(content)==digest)
        receipt=finalize_auto_acceptance(
            root=root,manifest=manifest,remote_files=remote_files,
            owner_grant=grant,grant_verify=signed_ok,
            source_version_verify=check_source,readback_verify=verify,_db=db)
        receipt['public_links_created']=False
        return receipt
