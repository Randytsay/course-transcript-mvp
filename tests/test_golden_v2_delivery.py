import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_delivery import checked_outputs, publish_granted_outputs
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.pipeline.golden_v2_quote_intake import wire
from app.providers.golden_v2_audio_staging import file_sha256


class FakeDrive:
    def __init__(self):
        self.files={'source':dict(id='source',version='7',size='3',md5Checksum='original-md5',parents=['parent'])}
        self.contents={}; self.generated=0; self.writes=[]; self.lose_response=False
    def get(self, key): return self.files.get(key)
    def named(self,parent,name): return [value for value in self.files.values() if value.get('name')==name and value.get('parents')==[parent]]
    def generate_id(self): self.generated+=1; return 'allocated-'+str(self.generated)
    def create_pending(self,key,parent,name,content,job_id):
        self.files[key]=dict(id=key,name=name,parents=[parent],appProperties={'golden_v2_job':job_id})
        self.contents[key]=content; self.writes.append(('create',key))
        if self.lose_response: self.lose_response=False; raise V2Conflict('simulated disconnected response')
    def readback(self,key): return self.contents[key]
    def write_pending_content(self,key,content): self.contents[key]=content; self.writes.append(('upload',key))
    def promote(self,key,name): self.files[key]['name']=name; self.writes.append(('promote',key))


class V2DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'v2'; self.root.mkdir(); self.lock=Path(self.temp.name)/'global'
        self.env=patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_GRANT_SECRET':'s'*40,'DRIVE_GLOBAL_MIN_INTERVAL_SECONDS':'0'})
        self.env.start(); self.addCleanup(self.env.stop)
        ledger=GoldenV2Ledger(self.root/'ledger.sqlite')
        costs=dict(gemini_primary=10,chirp_independent=10,compare=0,third_optional=0,adjudication=0,golden_qa=0)
        self.job_id=ledger.create_from_preflight(source_sha='a'*64,mode='FLASH_CHIRP',estimated_stage_cents=costs,registration_sha256='b'*64)
        ledger.approve(self.job_id,revision=0,approved_cents=20,reference='fake-test',actor='test')
        self.job=self.root/'jobs'/self.job_id; self.folder=self.job/'golden-v2'; self.folder.mkdir(parents=True)
        (self.job/'source-preflight.json').write_text(wire(dict(drive_source_path='gdrive:folder/251025_.MP3',
            drive_observation=dict(file_id='source',version='7',size_bytes=3,downloaded_md5='original-md5'))))
        (self.folder/'final.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n測試\n')
        (self.folder/'final-corrected-transcript.txt').write_text('測試')
        manifest=dict(source_sha256='a'*64,srt_sha256=file_sha256(self.folder/'final.srt'),
                      transcript_sha256=file_sha256(self.folder/'final-corrected-transcript.txt'))
        (self.folder/'render-manifest.json').write_text(wire(manifest))
        qa=dict(status='PASS',safe_publish_allowed=True,job_id=self.job_id,source_sha256='a'*64,
                render_sha256=file_sha256(self.folder/'render-manifest.json'))
        (self.folder/'golden-qa.json').write_text(wire(qa))
        while True:
            claim=ledger.claim_next(self.job_id,worker_id='test')
            digest=file_sha256(self.folder/'golden-qa.json') if claim['stage']=='golden_qa' else 'c'*64
            options={'comparison_has_dispute':False} if claim['stage']=='compare' else {}
            ledger.complete(claim,evidence_sha=digest,actual_cents=0,**options)
            if claim['stage']=='golden_qa': break
        state=ledger.status(self.job_id)
        grant=dict(job_id=self.job_id,source_sha256='a'*64,qa_sha256=file_sha256(self.folder/'golden-qa.json'),
                   render_sha256=file_sha256(self.folder/'render-manifest.json'),revision=state['revision'],
                   filenames=['251025_.srt','251025_逐字稿.txt'],actor='test',confirm_listened=True)
        (self.job/'owner-publish-grant.json').write_text(wire(signed_grant(grant)))
        self.gateway=FakeDrive()
    def publish(self): return publish_granted_outputs(root=self.root,job_id=self.job_id,lock_root=self.lock,gateway=self.gateway)

    def test_exact_selected_names_readback_and_restart_no_duplicate_or_public_link(self):
        result=self.publish(); self.assertEqual([f['name'] for f in result['files']],['251025_.srt','251025_逐字稿.txt'])
        self.assertFalse(result['public_links_created']); self.assertEqual(self.gateway.generated,2)
        writes=list(self.gateway.writes); self.assertEqual(self.publish(),result)
        self.assertEqual(self.gateway.writes,writes); self.assertEqual(self.gateway.generated,2)
        self.assertEqual(self.gateway.files['source']['version'],'7')

    def test_lost_create_response_reuses_saved_file_id_after_restart(self):
        self.gateway.lose_response=True
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.generated,1)
        self.assertEqual(self.publish()['status'],'DELIVERED'); self.assertEqual(self.gateway.generated,2)
        self.assertEqual(len([w for w in self.gateway.writes if w[0]=='create']),2)

    def test_existing_editor_target_is_never_changed(self):
        self.gateway.files['editor']=dict(id='editor',name='251025_.srt',parents=['parent'])
        self.gateway.contents['editor']=b'owner newer subtitle'
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.writes,[]); self.assertEqual(self.gateway.generated,0)
        self.assertEqual(self.gateway.contents['editor'],b'owner newer subtitle')

    def test_source_revision_change_or_final_artifact_forgery_blocks_before_write(self):
        self.gateway.files['source']['version']='8'
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.writes,[])
        self.gateway.files['source']['version']='7'
        (self.folder/'final.srt').write_text('forged')
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.writes,[])

    def test_loose_golden_pass_and_forged_owner_signature_are_insufficient(self):
        path=self.job/'owner-publish-grant.json'; value=json.loads(path.read_text()); value['signature']='0'*64; path.write_text(wire(value))
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.writes,[])


    def grant_revisions(self, names):
        grant_path=self.job/'owner-publish-grant.json'
        payload=json.loads(grant_path.read_text())['grant']
        old={}
        for i,name in enumerate(names):
            ident='editor-'+str(i)
            data=('human-authored-original-'+str(i)).encode()
            self.gateway.files[ident]=dict(
                id=ident,name=name,parents=['parent'],version='11',trashed=False,
            )
            self.gateway.contents[ident]=data
            dot=name.rfind('.')
            backup_name=name[:dot]+'(舊)'+name[dot:]
            old[name]={'file_id':ident,'version':'11',
                       'sha256':hashlib.sha256(data).hexdigest(),
                       'backup_name':backup_name}
        payload['existing_files']=old
        grant_path.write_text(wire(signed_grant(payload)))
        return old

    def test_existing_txt_requires_owner_attestation_before_any_new_srt_write(self):
        old_name='251025_逐字稿.txt'
        self.gateway.files['editor']=dict(
            id='editor',name=old_name,parents=['parent'],version='11',
        )
        self.gateway.contents['editor']=b'prior editorial text'
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.generated,0)
        self.assertEqual(self.gateway.writes,[])

    def test_signed_existing_txt_backup_then_new_srt_txt_and_idempotent_restart(self):
        name='251025_逐字稿.txt'
        original=self.grant_revisions([name])
        receipt=self.publish()
        self.assertEqual([b['backup_name'] for b in receipt['backups']],
                         [original[name]['backup_name']])
        self.assertEqual(self.gateway.files['editor-0']['name'],
                         '251025_逐字稿(舊).txt')
        self.assertEqual(
            hashlib.sha256(self.gateway.contents['editor-0']).hexdigest(),
            original[name]['sha256'])
        writes=list(self.gateway.writes)
        self.assertEqual(self.publish(),receipt)
        self.assertEqual(self.gateway.writes,writes)
        self.assertEqual(self.gateway.generated,2)

    def test_backup_both_existing_srt_and_txt_only_when_both_authorized(self):
        names=['251025_.srt','251025_逐字稿.txt']
        old=self.grant_revisions(names)
        receipt=self.publish()
        self.assertEqual(len(receipt['backups']),2)
        for i,name in enumerate(names):
            self.assertEqual(self.gateway.files['editor-'+str(i)]['name'],
                             old[name]['backup_name'])
            self.assertEqual(hashlib.sha256(
                self.gateway.contents['editor-'+str(i)]).hexdigest(),
                old[name]['sha256'])

    def test_human_edit_of_attested_old_txt_blocks_before_new_srt_created(self):
        self.grant_revisions(['251025_逐字稿.txt'])
        self.gateway.contents['editor-0']=b'new edits after approval'
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.generated,0)
        self.assertEqual(self.gateway.writes,[])

    def test_backup_name_conflict_blocks_before_any_write(self):
        old=self.grant_revisions(['251025_逐字稿.txt'])
        self.gateway.files['different-old']=dict(
            id='different-old',name=old['251025_逐字稿.txt']['backup_name'],
            parents=['parent'],version='3')
        self.gateway.contents['different-old']=b'existing unrelated backup'
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.generated,0)
        self.assertEqual(self.gateway.writes,[])

    def test_crash_between_old_backup_and_new_promotion_is_resumable(self):
        self.grant_revisions(['251025_逐字稿.txt'])
        original_promote=self.gateway.promote
        def interrupted(key,name):
            if name=='251025_逐字稿.txt':
                raise V2Conflict('simulated crash after old rename')
            return original_promote(key,name)
        self.gateway.promote=interrupted
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.files['editor-0']['name'],
                         '251025_逐字稿(舊).txt')
        self.assertEqual(self.gateway.generated,2)
        self.gateway.promote=original_promote
        result=self.publish()
        self.assertEqual(result['status'],'DELIVERED')
        self.assertEqual(self.gateway.generated,2)

    def test_second_pending_upload_failure_does_not_publish_first_output(self):
        self.grant_revisions(['251025_逐字稿.txt'])
        original_create=self.gateway.create_pending
        def fail_txt(key,parent,name,content,job_id):
            if name.endswith('.pending.txt'):
                raise V2Conflict('simulated second output upload failure')
            return original_create(key,parent,name,content,job_id)
        self.gateway.create_pending=fail_txt
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.files['editor-0']['name'],
                         '251025_逐字稿.txt')
        self.assertEqual(self.gateway.named('parent','251025_.srt'),[])
        self.assertTrue(self.gateway.get('allocated-1')['name'].endswith('.pending.srt'))
        self.assertFalse(any(v[0]=='promote' for v in self.gateway.writes))
        self.gateway.create_pending=original_create
        receipt=self.publish()
        self.assertEqual(receipt['status'],'DELIVERED')
        self.assertEqual(self.gateway.generated,2)

    def test_remote_edit_after_delivery_is_not_overwritten(self):
        result=self.publish(); key=result['files'][0]['file_id']; self.gateway.contents[key]=b'new human edit'
        previous=list(self.gateway.writes)
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.gateway.writes,previous)
