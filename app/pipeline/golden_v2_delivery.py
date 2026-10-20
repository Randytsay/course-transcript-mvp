"""Private V2 delivery: exact QA/source/grant, saved Drive IDs, readback hashes.

The first release creates new sidecars only. Existing editor/user files are
never overwritten, renamed or backed up without a separately reviewed revision
policy. Pending files retain their IDs across timeout/restart. No public links.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs import drive_api
from app.pipeline.golden_v2_quote_intake import wire
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_versioned_artifacts import resolve_versioned


def manifest_digest(manifest):
    return hashlib.sha256(wire(manifest).encode('utf-8')).hexdigest()


def checked_outputs(root, job_id):
    job=root/'jobs'/job_id; folder=job/'golden-v2'
    import re
    if (not re.fullmatch(r'v2p_[0-9a-f]{32}',job_id) or (root/'jobs').is_symlink()
        or job.is_symlink() or folder.is_symlink()): raise V2Conflict('unsafe delivery source')
    state=GoldenV2Ledger.open_existing(root/'ledger.sqlite').status(job_id)
    qa_stage=next(s for s in state['stages'] if s['name']=='golden_qa')
    if state['status']!='AWAITING_FINAL_REVIEW' or qa_stage['state']!='COMPLETE':
        raise V2Conflict('ledger-attested Golden QA required')
    qa_path=resolve_versioned(folder/'golden-qa.json',qa_stage['evidence_sha'])
    qa=json.loads(qa_path.read_text())
    manifest_path=resolve_versioned(folder/'render-manifest.json',qa.get('render_sha256'))
    manifest=json.loads(manifest_path.read_text())
    if (qa.get('status')!='PASS' or qa.get('safe_publish_allowed') is not True or
        qa.get('render_sha256')!=file_sha256(manifest_path) or
        qa.get('job_id')!=job_id or qa.get('source_sha256')!=state['source_sha'] or
        manifest.get('source_sha256')!=state['source_sha']):
        raise V2Conflict('Golden QA/manifest/source binding mismatch')
    preflight=json.loads((job/'source-preflight.json').read_text())
    source=preflight['drive_observation']; source_path=preflight['drive_source_path']
    stem=Path(source_path).stem
    names=[stem+'.srt',stem+('' if stem.endswith('_') else '_')+'逐字稿.txt']
    paths=[
        resolve_versioned(folder/'final.srt',manifest['srt_sha256']),
        resolve_versioned(folder/'final-corrected-transcript.txt',manifest['transcript_sha256']),
    ]
    for path,key in zip(paths,['srt_sha256','transcript_sha256']):
        if path.stat().st_size>10*1024**2 or file_sha256(path)!=manifest[key]:
            raise V2Conflict('immutable final artifact checksum changed')
    return state,source,source_path,qa_stage['evidence_sha'],manifest,dict(zip(names,paths))


class DriveDeliveryGateway:
    """OAuth is loaded privately; mutations target generated saved file IDs."""
    def __init__(self):
        import requests
        self.http=requests.Session()

    def request(self, method, path, *, params=None, payload=None, content=None):
        base='https://www.googleapis.com/drive/v3/'
        headers={'Authorization':'Bearer '+drive_api._refresh_access_token()}
        if content is not None: headers['Content-Type']='application/octet-stream'
        response=self.http.request(method,base+path,params=params,json=payload,data=content,
                                   headers=headers,timeout=60,allow_redirects=False)
        if response.status_code==404 and method=='GET': return None
        if not 200<=response.status_code<300:
            raise V2Conflict('Drive delivery HTTP operation requires reconciliation')
        return response.content if params and params.get('alt')=='media' else response.json()

    def get(self,file_id):
        return self.request('GET','files/'+file_id,params={'fields':'id,name,parents,size,md5Checksum,version,trashed,appProperties'})

    def generate_id(self):
        return self.request('GET','files/generateIds',params={'count':1,'space':'drive','type':'files'})['ids'][0]

    def named(self,parent,name):
        query="'"+drive_api._escape_query(parent)+"' in parents and name = '"+drive_api._escape_query(name)+"' and trashed = false"
        payload=self.request('GET','files',params={'q':query,'fields':'files(id,name,version),nextPageToken','pageSize':100})
        if payload.get('nextPageToken'): raise V2Conflict('ambiguous destination listing')
        return payload['files']

    def create_pending(self,file_id,parent,name,content,job_id):
        # Metadata intent is persisted before this POST. A lost response is
        # recovered by GET of this exact pre-generated ID, never a new POST ID.
        metadata={'id':file_id,'name':name,'parents':[parent],
                  'appProperties':{'golden_v2_job':job_id}}
        self.request('POST','files',payload=metadata)
        import requests
        response=self.http.patch('https://www.googleapis.com/upload/drive/v3/files/'+file_id,
                   params={'uploadType':'media'},data=content,
                   headers={'Authorization':'Bearer '+drive_api._refresh_access_token(),
                            'Content-Type':'application/octet-stream'},timeout=60,allow_redirects=False)
        if not 200<=response.status_code<300: raise V2Conflict('pending content upload requires reconciliation')

    def write_pending_content(self,file_id,content):
        response=self.http.patch('https://www.googleapis.com/upload/drive/v3/files/'+file_id,
                   params={'uploadType':'media'},data=content,
                   headers={'Authorization':'Bearer '+drive_api._refresh_access_token(),
                            'Content-Type':'application/octet-stream'},timeout=60,allow_redirects=False)
        if not 200<=response.status_code<300: raise V2Conflict('pending content upload requires reconciliation')

    def readback(self,file_id):
        return self.request('GET','files/'+file_id,params={'alt':'media'})

    def promote(self,file_id,name):
        return self.request('PATCH','files/'+file_id,payload={'name':name})


def publish_granted_outputs(*, root:Path, job_id:str, lock_root:Path, gateway=None):
    if root.is_symlink() or lock_root.is_symlink(): raise V2Conflict('unsafe shared publication lock')
    state,source,source_path,qa_sha,manifest,outputs=checked_outputs(root,job_id)
    job=root/'jobs'/job_id
    grant_path=job/'owner-publish-grant.json'
    if grant_path.is_symlink(): raise V2Conflict('unsafe publication grant')
    grant=json.loads(grant_path.read_text())
    payload=grant['grant']; expected=signed_grant(payload)['signature']
    if (not hmac.compare_digest(expected,grant['signature']) or payload['job_id']!=job_id or
        payload['source_sha256']!=state['source_sha'] or payload['qa_sha256']!=qa_sha or
        payload['render_sha256']!=manifest_digest(manifest) or
        payload['revision']!=state['revision'] or payload['filenames']!=list(outputs) or
        not payload['actor'] or payload['confirm_listened'] is not True):
        raise V2Conflict('authenticated final source/revision/publication grant required')
    gateway=gateway or DriveDeliveryGateway()
    def check_source():
        live=gateway.get(source['file_id'])
        if (not live or live.get('trashed') or str(live.get('version'))!=str(source['version']) or
            int(live.get('size',-1))!=source['size_bytes'] or live.get('md5Checksum')!=source['downloaded_md5'] or
            len(live.get('parents',[]))!=1):
            raise V2Conflict('Drive source revision changed before delivery')
        return live['parents'][0]
    with drive_publish_lock(lock_root,source_path):
        parent=check_source(); delivered=[]
        for name,path in outputs.items():
            content=path.read_bytes(); sha=hashlib.sha256(content).hexdigest()
            intent=job/'golden-v2'/(('srt' if name.endswith('.srt') else 'txt')+'-delivery-intent.json')
            if intent.is_symlink(): raise V2Conflict('unsafe delivery intent')
            if intent.exists():
                planned=json.loads(intent.read_text()); file_id=planned['file_id']
                if planned['sha256']!=sha or planned['name']!=name or planned['parent_id']!=parent:
                    raise V2Conflict('publication intent mismatch')
            else:
                if gateway.named(parent,name): raise V2Conflict('destination exists; separate revision approval required')
                file_id=gateway.generate_id()
                planned={'file_id':file_id,'parent_id':parent,'name':name,'sha256':sha,
                         'pending_name':'.'+job_id+'.pending.'+('srt' if name.endswith('.srt') else 'txt')}
                store_immutable_bytes(intent,wire(planned).encode())
            matches=gateway.named(parent,name)
            if any(item['id']!=file_id for item in matches):
                raise V2Conflict('new editor revision exists; publication held')
            remote=gateway.get(file_id)
            if remote is None:
                check_source()
                gateway.create_pending(file_id,parent,planned['pending_name'],content,job_id)
                remote=gateway.get(file_id)
            if (not remote or remote.get('appProperties',{}).get('golden_v2_job')!=job_id or
                remote.get('parents')!=[parent] or remote.get('trashed')):
                raise V2Conflict('saved pending ID not owned by this transaction')
            observed=gateway.readback(file_id)
            if not isinstance(observed,bytes): raise V2Conflict('remote readback unavailable')
            if hashlib.sha256(observed).hexdigest()!=sha:
                # Never replace a promoted/edited file. Only a still-empty own
                # pending object can recover a crash between create and upload.
                if remote.get('name')!=planned['pending_name'] or observed:
                    raise V2Conflict('remote content changed; do not overwrite')
                check_source(); gateway.write_pending_content(file_id,content)
                if hashlib.sha256(gateway.readback(file_id)).hexdigest()!=sha:
                    raise V2Conflict('pending readback checksum mismatch')
            if remote['name']!=name:
                if remote['name']!=planned['pending_name']: raise V2Conflict('remote pending name edited')
                check_source()
                if gateway.named(parent,name): raise V2Conflict('destination changed before promote')
                gateway.promote(file_id,name)
            verified=gateway.get(file_id)
            if verified['name']!=name or hashlib.sha256(gateway.readback(file_id)).hexdigest()!=sha or any(item['id']!=file_id for item in gateway.named(parent,name)):
                raise V2Conflict('final name/hash/conflict verification failed')
            delivered.append({'name':name,'file_id':file_id,'sha256':sha,'bytes':len(content)})
        check_source()
        receipt={'schema':'golden-v2-drive-delivery-v1','job_id':job_id,'source_sha256':state['source_sha'],
                 'qa_sha256':qa_sha,'files':delivered,'status':'DELIVERED','public_links_created':False}
        store_immutable_bytes(job/'golden-v2/drive-delivery.json',wire(receipt).encode())
        return receipt
