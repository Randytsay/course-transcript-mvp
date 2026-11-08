"""Four-asset fail-closed publication and receipt tests (no Drive network)."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_auto_closeout import _wire, prepare_auto_acceptance
from app.pipeline.golden_v2_auto_publication import publish_signed_auto_acceptance
from app.providers.golden_v2_auto_acceptance import render_human_review_advisory
from tests.test_golden_v2_auto_closeout import CloseoutTests


def sign(payload):
    return {'grant':payload,'signature':hashlib.sha256(_wire(payload)).hexdigest()}


class FakeDrive:
    def __init__(self,source_bytes):
        self.source_bytes=source_bytes
        self.files={'source-id':{'id':'source-id','version':'7','size':str(len(source_bytes)),
             'md5Checksum':'original-md5','name':'lesson.mp3','parents':['original-folder'],
             'trashed':False}}
        self.data={};self.generated=0;self.block_fourth=False;self.promotions=[]

    def get(self,file_id): return self.files.get(file_id)
    def named(self,parent,name):
        return [{'id':x['id'],'name':x['name']} for x in self.files.values()
                if x.get('name')==name and x.get('parents')==[parent] and not x.get('trashed')]
    def generate_id(self):
        self.generated+=1
        return 'allocated-'+str(self.generated)
    def create_pending(self,file_id,parent,name,content,job_id):
        if self.block_fourth and file_id=='allocated-4':
            raise V2Conflict('simulated fourth-file outage')
        self.files[file_id]={'id':file_id,'name':name,'parents':[parent],
                        'appProperties':{'golden_v2_job':job_id},'trashed':False}
        self.data[file_id]=content
    def readback(self,file_id):return self.data.get(file_id,b'')
    def write_pending_content(self,file_id,raw):self.data[file_id]=raw
    def promote(self,file_id,name):
        self.files[file_id]['name']=name
        self.promotions.append(file_id)


class AutoPublisherTests(unittest.TestCase):
    def setUp(self):
        CloseoutTests.setUp(self)
        original=json.loads((self.jobdir/'source-preflight.json').read_text())
        original['drive_observation']['size_bytes']=len(self.flac)
        original['drive_observation']['downloaded_md5']='original-md5'
        original['original_md5_checksum']='original-md5'
        original['drive_source_path']='gdrive:original-folder/lesson.mp3'
        (self.jobdir/'source-preflight.json').write_text(json.dumps(original))
        folder=self.jobdir/'golden-v2';folder.mkdir(exist_ok=True)
        self.srt0=folder/self.srt.name;self.srt0.write_bytes(self.srt.read_bytes())
        self.txt0=folder/self.txt.name;self.txt0.write_bytes(self.txt.read_bytes())
        self.result['human_review_advisory']['recommended']=True
        self.report_json=folder/'acceptance.json'
        self.report_json.write_bytes(_wire(self.result))
        self.report_md=folder/'human-review.md'
        self.report_md.write_bytes(render_human_review_advisory(self.result,lesson_title='測試課程').encode())
        self.extra_names=('output_自動驗收_驗收.json','output_自動驗收_人工建議複核.md')
        self.prepared=prepare_auto_acceptance(root=self.root,job_id=self.job,
            result=self.result,srt_path=self.srt0,txt_path=self.txt0,
            target_names=self.names,report_json_path=self.report_json,
            report_md_path=self.report_md,report_names=self.extra_names,lesson_title='測試課程')
        self.manifest=self.prepared['manifest']
        self.grant={**CloseoutTests.grant(self,self.manifest),'source_version_at_approval':'7',
                    'human_listening_attested':False}
        grant_path=self.jobdir/'owner-auto-delivery-grants'/(
            self.manifest['manifest_sha256']+'.json')
        grant_path.parent.mkdir(exist_ok=True)
        grant_path.write_bytes(_wire(sign(self.grant)))
        self.drive=FakeDrive(self.flac)

    def publish(self):
        with patch('app.pipeline.golden_v2_auto_publication.signed_grant',sign):
            return publish_signed_auto_acceptance(root=self.root,job_id=self.job,
                 manifest_sha=self.manifest['manifest_sha256'],lock_root=self.root,
                 gateway=self.drive)

    def test_real_drive_gateway_is_disabled_without_atomic_mutations(self):
        from app.pipeline.golden_v2_delivery import DriveDeliveryGateway
        gateway=DriveDeliveryGateway.__new__(DriveDeliveryGateway)
        for name in ('get','named','generate_id','create_pending','readback','promote'):
            setattr(gateway,name,getattr(self.drive,name))
        with patch('app.pipeline.golden_v2_auto_publication.signed_grant',sign):
            with self.assertRaises(V2Conflict):
                publish_signed_auto_acceptance(root=self.root,job_id=self.job,
                    manifest_sha=self.manifest['manifest_sha256'],lock_root=self.root,
                    gateway=gateway)
        self.assertEqual(set(self.drive.files),{'source-id'})
        self.assertEqual(self.drive.generated,0)

    def test_repeated_generated_id_is_rejected_before_remote_writes(self):
        self.drive.generate_id=lambda: 'allocated-1'
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertEqual(set(self.drive.files),{'source-id'})
        self.assertFalse(self.drive.promotions)

    def test_empty_pending_after_create_is_never_overwritten(self):
        def create_empty(file_id, parent, name, content, job_id):
            self.drive.files[file_id]={'id':file_id,'name':name,'parents':[parent],
                'appProperties':{'golden_v2_job':job_id},'trashed':False}
            self.drive.data[file_id]=b''
        self.drive.create_pending=create_empty
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertEqual(self.drive.data['allocated-1'],b'')
        self.assertFalse(self.drive.promotions)

    def test_four_files_and_idempotent_receipt(self):
        result=self.publish()
        self.assertEqual(result['state'],'AUTO_DELIVERED')
        self.assertEqual(len(result['remote_files']),4)
        self.assertFalse(result['golden_qa_pass'])
        self.assertFalse(result['human_listening_attested'])
        self.assertEqual(self.drive.generated,4)
        promotions=list(self.drive.promotions)
        self.assertEqual(self.publish()['manifest_sha256'],result['manifest_sha256'])
        self.assertEqual(self.drive.promotions,promotions)
        self.assertEqual(self.ledger.status(self.job)['status'],'PROCESSING')

    def test_fourth_file_retry_without_authoritative_id_history_stays_fenced(self):
        self.drive.block_fourth=True
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,4)
        self.assertFalse(self.drive.promotions)
        self.assertEqual(self.prepared['state'],'PREPARED')
        self.drive.block_fourth=False
        with self.assertRaisesRegex(V2Conflict,'ambiguous history'):
            self.publish()
        self.assertEqual(self.drive.generated,4)

    def test_tampered_persisted_intent_cannot_publish_direct_to_final_name(self):
        intent_dir=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        intent_dir.mkdir(parents=True)
        plan={'file_id':'allocated-1','sha256':self.manifest['srt_sha256'],
              'parent_id':'original-folder','name':self.names[0],
              'pending_name':self.names[0]}
        (intent_dir/'0.json').write_bytes(_wire(plan))
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(len(self.drive.files),1)
        self.assertFalse(self.drive.promotions)

    def test_existing_editor_file_is_never_overwritten(self):
        self.drive.files['original-srt']={'id':'original-srt',
            'name':self.names[0],'parents':['original-folder'],'trashed':False}
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)

    def test_changed_audio_version_blocks_before_drive_write(self):
        self.drive.files['source-id']['version']='8'
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)

    def test_crash_after_promotion_leaves_job_fenced(self):
        import multiprocessing, os, sqlite3
        def child():
            promote=self.drive.promote
            def crash(ident,name):
                promote(ident,name)
                os._exit(31)
            self.drive.promote=crash
            self.publish()
        proc=multiprocessing.get_context('fork').Process(target=child)
        proc.start(); proc.join(20)
        if proc.is_alive():
            proc.terminate(); proc.join(); self.fail('child did not exit')
        self.assertEqual(proc.exitcode,31)
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM v2_auto_publication_fences WHERE job_id=?',(self.job,)).fetchone())
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE v2_jobs SET revision=revision+1 WHERE id=?',(self.job,))
        with self.assertRaises(V2Conflict): self.publish()

    def test_source_change_during_final_readback_prevents_receipt(self):
        import sqlite3
        readback=self.drive.readback
        reads=[]
        def changing_readback(ident):
            value=readback(ident)
            reads.append(ident)
            if len(reads)==12:
                self.drive.files['source-id']['version']='8'
            return value
        self.drive.readback=changing_readback
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(len(reads),12)
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            state,receipt=db.execute('SELECT state,remote_receipt_json FROM v2_auto_acceptance_deliveries WHERE job_id=?',(self.job,)).fetchone()
            self.assertEqual(state,'PREPARED')
            self.assertIsNone(receipt)
            self.assertIsNotNone(db.execute('SELECT 1 FROM v2_auto_publication_fences WHERE job_id=?',(self.job,)).fetchone())

    def test_source_parent_change_mid_promotion_prevents_receipt(self):
        import sqlite3
        promote=self.drive.promote
        def move_source(ident,name):
            promote(ident,name)
            self.drive.files['source-id']['parents']=['different-folder']
        self.drive.promote=move_source
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(len(self.drive.promotions),1)
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            state,receipt=db.execute('SELECT state,remote_receipt_json FROM v2_auto_acceptance_deliveries WHERE job_id=?',(self.job,)).fetchone()
            self.assertEqual(state,'PREPARED')
            self.assertIsNone(receipt)

    def test_source_change_mid_promotion_leaves_partial_visibility(self):
        promote=self.drive.promote
        def change_source(ident,name):
            promote(ident,name)
            self.drive.files['source-id']['version']='8'
        self.drive.promote=change_source
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(len(self.drive.promotions),1)
        self.assertEqual(sum(x.get('name') in (*self.names,*self.extra_names)
                             for x in self.drive.files.values()),1)
        with self.assertRaises(V2Conflict): self.publish()

    def test_fence_rejects_update_or_replace_into_fenced_job(self):
        import sqlite3
        other='v2p_'+'e'*32
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute('INSERT INTO v2_jobs(id,source_sha,mode,estimated_cents,created_at) '
                       'SELECT ?,source_sha,mode,estimated_cents,created_at FROM v2_jobs WHERE id=?',
                       (other,self.job))
        def ambiguous(*args): raise TimeoutError('outcome unknown')
        self.drive.create_pending=ambiguous
        with self.assertRaises(TimeoutError): self.publish()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE OR REPLACE v2_jobs SET id=? WHERE id=?',(self.job,other))

    def test_uncertain_publication_cannot_be_finalized_by_separate_caller(self):
        from app.pipeline.golden_v2_auto_closeout import finalize_auto_acceptance
        def ambiguous(*args): raise TimeoutError('outcome unknown')
        self.drive.create_pending=ambiguous
        with self.assertRaises(TimeoutError): self.publish()
        names=[*self.names,*self.extra_names]
        with self.assertRaises(V2Conflict):
            finalize_auto_acceptance(root=self.root,manifest=self.manifest,
                remote_files=[{'name':n,'file_id':n} for n in names],
                owner_grant=self.grant,grant_verify=lambda g:True,
                source_version_verify=lambda *a:True,readback_verify=lambda *a:True)

    def test_failure_keeps_stage_fenced_across_processes(self):
        import sqlite3, subprocess, sys
        code=("import sqlite3,sys; db=sqlite3.connect(sys.argv[1],timeout=2); "
              "db.execute(\"UPDATE v2_stages SET updated_at='probe' WHERE job_id=?\",(sys.argv[2],)); db.commit()")
        def update_in_process():
            return subprocess.run([sys.executable,'-c',code,str(self.root/'ledger.sqlite'),self.job],capture_output=True,text=True)
        def ambiguous(*args): raise TimeoutError('outcome unknown')
        self.drive.create_pending=ambiguous
        with self.assertRaises(TimeoutError): self.publish()
        failed=update_in_process()
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('auto publication needs reconciliation',failed.stderr)
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            self.assertEqual(db.execute('SELECT state FROM v2_auto_acceptance_deliveries WHERE job_id=?',(self.job,)).fetchone()[0],'PREPARED')

    def test_fence_release_failure_rolls_back_receipt(self):
        import sqlite3
        from app.pipeline import golden_v2_auto_publication as publication
        finalize=publication.finalize_auto_acceptance
        def sabotage_release(**kwargs):
            receipt=finalize(**kwargs)
            kwargs['_db'].execute("CREATE TRIGGER test_no_release BEFORE DELETE ON v2_auto_publication_fences BEGIN SELECT RAISE(ABORT, 'release failed'); END")
            return receipt
        with patch.object(publication,'finalize_auto_acceptance',side_effect=sabotage_release):
            with self.assertRaises(sqlite3.IntegrityError): self.publish()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            self.assertEqual(db.execute('SELECT state,remote_receipt_json FROM v2_auto_acceptance_deliveries WHERE job_id=?',(self.job,)).fetchone(),('PREPARED',None))
            self.assertIsNotNone(db.execute('SELECT 1 FROM v2_auto_publication_fences WHERE job_id=?',(self.job,)).fetchone())

    def test_successful_publication_releases_fence_for_stage_writes(self):
        import sqlite3
        self.publish()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            self.assertIsNone(db.execute('SELECT 1 FROM v2_auto_publication_fences WHERE job_id=?',(self.job,)).fetchone())
            self.assertGreater(db.execute("UPDATE v2_stages SET updated_at='probe' WHERE job_id=?",(self.job,)).rowcount,0)

    def test_legacy_fence_gets_stage_protection_even_when_retry_refused(self):
        import sqlite3
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute('CREATE TABLE v2_auto_publication_fences (job_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL)')
            db.execute('INSERT INTO v2_auto_publication_fences VALUES (?,?)',(self.job,self.manifest['manifest_sha256']))
        with self.assertRaises(V2Conflict): self.publish()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE v2_stages SET evidence_sha='changed' WHERE job_id=?",(self.job,))

    def test_uncertain_publication_fences_stage_mutations(self):
        import sqlite3
        def ambiguous(*args): raise TimeoutError('outcome unknown')
        self.drive.create_pending=ambiguous
        with self.assertRaises(TimeoutError): self.publish()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE v2_stages SET evidence_sha='changed' WHERE job_id=?",(self.job,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM v2_stages WHERE job_id=?',(self.job,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO v2_stages(job_id,ord,name) VALUES (?,999,'new')",(self.job,))

    def test_ambiguous_drive_failure_fences_later_revision_writes(self):
        import sqlite3
        def ambiguous(*args):
            raise TimeoutError('outcome unknown')
        self.drive.create_pending=ambiguous
        with self.assertRaises(TimeoutError): self.publish()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE v2_jobs SET revision=revision+1 WHERE id=?',(self.job,))
        with self.assertRaises(V2Conflict): self.publish()

    def test_other_process_cannot_change_revision_during_drive_operations(self):
        import subprocess, sys
        code = ("import sqlite3,sys; db=sqlite3.connect(sys.argv[1], timeout=0); "
                "db.execute('UPDATE v2_jobs SET revision=revision+1 WHERE id=?',(sys.argv[2],)); db.commit()")
        attempted=[]
        def try_update(label):
            p=subprocess.run([sys.executable,'-c',code,str(self.root/'ledger.sqlite'),self.job],
                             capture_output=True,text=True)
            attempted.append((label,p.returncode,p.stderr))
        for name in ('generate_id','create_pending','promote','readback'):
            original=getattr(self.drive,name)
            def wrapper(*args, _name=name, _original=original, **kwargs):
                try_update(_name)
                return _original(*args,**kwargs)
            setattr(self.drive,name,wrapper)
        try:
            self.publish()
        except V2Conflict:
            pass
        self.assertTrue(attempted)
        self.assertEqual({x[0] for x in attempted},{'generate_id','create_pending','promote','readback'})
        self.assertTrue(all(rc != 0 and 'locked' in err for _,rc,err in attempted),attempted)

    def test_stale_revision_never_mutates_drive(self):
        import sqlite3
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute('UPDATE v2_jobs SET revision=revision+1 WHERE id=?',(self.job,))
        with self.assertRaises(V2Conflict): self.publish()
        self.assertEqual(self.drive.generated,0)
        self.assertEqual(len(self.drive.files),1)
        self.assertFalse(self.drive.promotions)

    def test_valid_signature_wrong_scope_never_mutates_drive(self):
        grant_path=self.jobdir/'owner-auto-delivery-grants'/(self.manifest['manifest_sha256']+'.json')
        for key, value in [('job_id','v2p_'+'f'*32), ('manifest_sha256','f'*64),
                           ('source_sha256','f'*64), ('quality_tier','wrong'),
                           ('output_names',['wrong']), ('approve_auto_delivery',False),
                           ('actor',''), ('reference','')]:
            with self.subTest(key=key):
                grant_path.write_bytes(_wire(sign({**self.grant,key:value})))
                with self.assertRaises(V2Conflict): self.publish()
                self.assertEqual(self.drive.generated,0)
                self.assertFalse(self.drive.promotions)
                self.assertEqual(len(self.drive.files),1)
                self.assertFalse((self.jobdir/'golden-v2'/'auto-delivery-intents').exists())

    def test_forged_grant_blocks(self):
        grant_path=self.jobdir/'owner-auto-delivery-grants'/(
            self.manifest['manifest_sha256']+'.json')
        envelope=json.loads(grant_path.read_text())
        envelope['signature']='0'*64
        grant_path.write_text(json.dumps(envelope))
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)

    def test_modified_companion_blocks_before_publication(self):
        self.report_md.write_text('forged')
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)
