"""Owner-signed, resumable archival of exact old Drive revisions after delivery.

No provider calls or Golden QA/listening claims. All remote operations are under
 the same Drive publication lock; a persisted plan fences retries after a crash.
"""
from __future__ import annotations

import hmac
import json
import re
from pathlib import Path

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_ai_limited_closeout import _check_file, _sha, _wire
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway
from app.pipeline.golden_v2_revision_backup import verified_backup_plan, verify_and_backup_old
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes

PURPOSE = 'ARCHIVE_DELIVERED_AI_LIMITED_OLD_REVISIONS'


def archive_signed_old_revisions(*, root: Path, job_id: str, manifest_sha: str,
                                 lock_root: Path, gateway=None) -> dict:
    root, lock_root = Path(root), Path(lock_root)
    if (root.is_symlink() or lock_root.is_symlink() or
            not re.fullmatch(r'v2p_[0-9a-f]{32}', job_id) or
            not re.fullmatch(r'[0-9a-f]{64}', manifest_sha)):
        raise V2Conflict('unsafe archive scope')
    job = root/'jobs'/job_id
    folder = job/'golden-v2'
    if job.is_symlink() or folder.is_symlink():
        raise V2Conflict('unsafe archive job')
    path = job/'owner-ai-limited-old-archive-grant'/(manifest_sha+'.json')
    envelope = json.loads(_check_file(path, 32768))
    grant = envelope.get('grant')
    if (not isinstance(grant, dict) or not isinstance(envelope.get('signature'), str)
            or not hmac.compare_digest(signed_grant(grant)['signature'], envelope['signature'])):
        raise V2Conflict('old revision owner signature missing or invalid')
    ledger = GoldenV2Ledger.open_existing(root/'ledger.sqlite')
    with ledger._tx() as db:
        row = db.execute('SELECT state,manifest_json,remote_receipt_json FROM '
                         'v2_ai_limited_deliveries WHERE job_id=? AND manifest_sha=?',
                         (job_id, manifest_sha)).fetchone()
        if not row or row['state'] != 'DELIVERED' or not row['remote_receipt_json']:
            raise V2Conflict('verified AI-limited delivery required before archive')
        manifest = json.loads(row['manifest_json'])
        delivered = json.loads(row['remote_receipt_json'])
    state = ledger.status(job_id)
    if (manifest.get('manifest_sha256') != manifest_sha or
            manifest.get('job_id') != job_id or
            state['revision'] != manifest['ledger_revision'] or
            state['source_sha'] != manifest['source_sha256'] or
            state['status'] != manifest['golden_job_status'] or
            delivered.get('manifest_sha256') != manifest_sha or
            delivered.get('readback_verified') is not True):
        raise V2Conflict('delivered ledger or source revision drift')
    old = grant.get('old_files')
    if (grant.get('purpose') != PURPOSE or grant.get('job_id') != job_id or
            grant.get('manifest_sha256') != manifest_sha or
            grant.get('source_sha256') != manifest['source_sha256'] or
            not str(grant.get('actor') or '').strip() or
            not str(grant.get('reference') or '').strip() or
            not isinstance(old, list) or len(old) != 2 or
            any(not isinstance(x, dict) or set(x) !=
                {'file_id','name','version','sha256','backup_name'} for x in old) or
            len({x['name'] for x in old}) != 2 or
            len({x['file_id'] for x in old}) != 2 or
            {Path(x['name']).suffix.lower() for x in old} != {'.srt','.txt'}):
        raise V2Conflict('old revision grant scope incomplete')
    preflight = json.loads(_check_file(job/'source-preflight.json', 262144))
    obs = preflight['drive_observation']
    if (obs['file_id'] != manifest['source_file_id'] or
            str(obs['version']) != manifest['source_version'] or
            preflight['source_sha256'] != manifest['source_sha256']):
        raise V2Conflict('original source preflight drift')
    gateway = gateway or DriveDeliveryGateway()
    names = {x['name']: {k: x[k] for k in
             ('file_id','version','sha256','backup_name')} for x in old}
    receipt_path = folder/'ai-limited-old-archive-receipts'/(manifest_sha+'.json')
    if receipt_path.is_symlink() or receipt_path.parent.is_symlink():
        raise V2Conflict('unsafe archive receipt')

    with drive_publish_lock(lock_root, preflight['drive_source_path']):
        def check_source():
            remote = gateway.get(obs['file_id'])
            if (not remote or remote.get('trashed') or remote.get('id') != obs['file_id']
                    or str(remote.get('version')) != str(grant.get('source_version_at_approval'))
                    or int(remote.get('size', -1)) != int(obs['size_bytes'])
                    or remote.get('md5Checksum') != obs.get('downloaded_md5',
                        preflight.get('original_md5_checksum'))
                    or len(remote.get('parents', [])) != 1):
                raise V2Conflict('Drive source drift before archive')
            return remote['parents'][0]

        parent = check_source()
        files = delivered.get('files')
        expected = dict(zip(manifest['output_names'],
                            (manifest['srt_sha256'],manifest['txt_sha256'])))
        if (not isinstance(files, list) or len(files) != 2 or
                {x.get('name') for x in files} != set(expected) or
                len({x.get('id') for x in files}) != 2):
            raise V2Conflict('delivered remote IDs differ from manifest')

        def check_delivery():
            if check_source() != parent:
                raise V2Conflict('source parent changed')
            for item in files:
                name, file_id = item['name'], item['id']
                remote = gateway.get(file_id)
                if (not remote or remote.get('id') != file_id or remote.get('trashed')
                        or remote.get('name') != name or remote.get('parents') != [parent]
                        or (remote.get('appProperties') or {}).get('golden_v2_job') != job_id
                        or {x['id'] for x in gateway.named(parent,name)} != {file_id}
                        or _sha(gateway.readback(file_id)) != expected[name]):
                    raise V2Conflict('new delivery remote ID or bytes drift')
        check_delivery()
        # First prove both old revisions and both backup names without mutation.
        plan = verified_backup_plan(gateway=gateway,parent=parent,
            outputs={name:None for name in names},approved=names,saved_ids={})
        if set(names) & set(expected) or any(x['file_id'] in
                {f['id'] for f in files} | {obs['file_id']} for x in old):
            raise V2Conflict('archive IDs overlap source or delivered files')
        receipt = {'schema':'golden-v2-limited-old-archive-v1',
            'state':'ARCHIVED','job_id':job_id,'manifest_sha256':manifest_sha,
            'parent_id':parent,'owner_grant_sha256':_sha(_wire(grant)),
            'old_files':[{'original_name':x['name'],'backup_name':x['backup_name'],
                'file_id':x['file_id'],'sha256':x['sha256']} for x in old]}
        if receipt_path.is_file():
            if _check_file(receipt_path,32768) != _wire(receipt):
                raise V2Conflict('immutable archive receipt conflict')
        for item in old:
            check_delivery()
            verify_and_backup_old(gateway=gateway,parent=parent,name=item['name'],
                                  selected=plan[item['name']],check_source=check_delivery)
        check_delivery()
        # Recheck both old files after the last rename, including unique names.
        verified_backup_plan(gateway=gateway,parent=parent,
            outputs={name:None for name in names},approved=names,saved_ids={})
        receipt_path.parent.mkdir(parents=True,exist_ok=True)
        store_immutable_bytes(receipt_path,_wire(receipt))
        return receipt
