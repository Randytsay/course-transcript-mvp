"""Cross-process, persistent synthetic Drive crash-recovery specifications.

These tests exercise the publisher with a persistent synthetic gateway only;
no external services are used.
"""
import hashlib
import json
import multiprocessing
import os
import sqlite3
import unittest
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
import tests.test_golden_v2_auto_publication as publication_fixtures


class PersistentDrive:
    """Tiny durable Drive surrogate; each operation commits before it returns."""
    def __init__(self, path, source_bytes=None, crash_at=None):
        self.path = path
        self.crash_at = crash_at
        if source_bytes is not None:
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE objects (id TEXT PRIMARY KEY, metadata TEXT NOT NULL, content BLOB NOT NULL)')
                db.execute('CREATE TABLE counters (n INTEGER NOT NULL)')
                db.execute('CREATE TABLE allocated_ids (id TEXT PRIMARY KEY, created INTEGER NOT NULL CHECK(created IN (0,1)))')
                db.execute('CREATE TABLE inaccessible_ids (id TEXT PRIMARY KEY)')
                db.execute('CREATE TABLE create_attempts (id TEXT NOT NULL)')
                db.execute('INSERT INTO counters VALUES (0)')
                source = {'id':'source-id', 'version':'7', 'size':str(len(source_bytes)),
                          'md5Checksum':'original-md5', 'name':'lesson.mp3',
                          'parents':['original-folder'], 'trashed':False}
                db.execute('INSERT INTO objects VALUES (?,?,?)', ('source-id',json.dumps(source),b''))

    def _save(self, ident, metadata, content):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?)',
                       (ident,json.dumps(metadata,sort_keys=True),content))

    def get(self, ident):
        with sqlite3.connect(self.path) as db:
            if db.execute('SELECT 1 FROM inaccessible_ids WHERE id=?',(ident,)).fetchone():
                return None  # Model Drive's ambiguous GET 404, not deletion.
            row = db.execute('SELECT metadata FROM objects WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def named(self, parent, name):
        with sqlite3.connect(self.path) as db:
            rows = db.execute('SELECT metadata FROM objects WHERE id NOT IN (SELECT id FROM inaccessible_ids)').fetchall()
        objects = [json.loads(row[0]) for row in rows]
        return [{'id':obj['id'],'name':obj['name']} for obj in objects
                if obj.get('parents') == [parent] and obj.get('name') == name and not obj.get('trashed')]

    def generate_id(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE counters SET n=n+1')
            ident='allocated-'+str(db.execute('SELECT n FROM counters').fetchone()[0])
            db.execute('INSERT INTO allocated_ids VALUES (?,0)',(ident,))
        if self.crash_at == 'allocation' and ident == 'allocated-2':
            os._exit(34)
        return ident

    def prove_never_created(self, ident):
        # Synthetic-only authoritative history: even a deleted object leaves
        # a durable tombstone. Drive's ordinary 404 provides no such proof.
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT created FROM allocated_ids WHERE id=?',(ident,)).fetchone()==(0,)

    def create_pending(self, ident, parent, name, content, job_id):
        if self.crash_at == 'fourth' and ident == 'allocated-4':
            os._exit(31)
        metadata={'id':ident, 'name':name, 'parents':[parent],
                  'appProperties':{'golden_v2_job':job_id}, 'trashed':False}
        with sqlite3.connect(self.path) as db:
            if db.execute('UPDATE allocated_ids SET created=1 WHERE id=? AND created=0',(ident,)).rowcount!=1:
                raise V2Conflict('duplicate or unreserved generated ID')
            db.execute('INSERT INTO create_attempts VALUES (?)',(ident,))
            db.execute('INSERT INTO objects VALUES (?,?,?)',(ident,json.dumps(metadata,sort_keys=True),content))
        if self.crash_at == 'created_timeout' and ident == 'allocated-1':
            os._exit(35)  # Server committed; client dies before observing response.

    def readback(self, ident):
        with sqlite3.connect(self.path) as db:
            if db.execute('SELECT 1 FROM inaccessible_ids WHERE id=?',(ident,)).fetchone():
                return None
            row=db.execute('SELECT content FROM objects WHERE id=?',(ident,)).fetchone()
        return bytes(row[0]) if row else None

    def write_pending_content(self, ident, content):
        self._save(ident,self.get(ident),content)

    def promote(self, ident, name):
        obj=self.get(ident); obj['name']=name
        self._save(ident,obj,self.readback(ident))
        if self.crash_at == 'promotion':
            os._exit(32)


class ProcessRecoveryTests(unittest.TestCase):
    setUp = publication_fixtures.AutoPublisherTests.setUp
    publish = publication_fixtures.AutoPublisherTests.publish

    def _child(self, crash_at=None):
        self.drive=PersistentDrive(self.remote_path,crash_at=crash_at)
        if crash_at == 'receipt':
            from app.pipeline import golden_v2_auto_publication as publication
            original=publication.finalize_auto_acceptance
            def crash_after_receipt(**kwargs):
                original(**kwargs)
                os._exit(33)
            with patch.object(publication,'finalize_auto_acceptance',side_effect=crash_after_receipt):
                self.publish()
        else:
            try:
                result=self.publish()
                self.result_path.write_text(json.dumps({'receipt':result}))
            except Exception as exc:
                self.result_path.write_text(json.dumps({'error':str(exc), 'type':type(exc).__name__}))

    def _run(self, crash_at=None):
        self.result_path.unlink(missing_ok=True)
        p=multiprocessing.get_context('fork').Process(target=self._child,args=(crash_at,))
        p.start(); p.join(20)
        if p.is_alive():
            p.terminate(); p.join(); self.fail('publication child hung')
        return p.exitcode, json.loads(self.result_path.read_text()) if self.result_path.exists() else None

    def _initialize(self):
        self.remote_path=self.root/'fake-drive.sqlite'
        self.result_path=self.root/'child-result.json'
        self.drive=PersistentDrive(self.remote_path,self.flac)

    def _state(self):
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            state=db.execute('SELECT state,remote_receipt_json FROM v2_auto_acceptance_deliveries WHERE job_id=?',(self.job,)).fetchone()
            fence=db.execute('SELECT manifest_sha FROM v2_auto_publication_fences WHERE job_id=?',(self.job,)).fetchone()
            revision=db.execute('SELECT revision FROM v2_jobs WHERE id=?',(self.job,)).fetchone()[0]
            stages=db.execute('SELECT * FROM v2_stages WHERE job_id=? ORDER BY ord',(self.job,)).fetchall()
        return state,fence,revision,stages

    def test_fourth_file_crash_retry_reuses_all_ids_and_produces_receipt(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        before=self._state()
        self.assertEqual(before[0],('PREPARED',None))
        self.assertEqual(before[1],(self.manifest['manifest_sha256'],))
        intent=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        plans=[json.loads((intent/(str(i)+'.json')).read_text()) for i in range(4)]
        self.assertEqual([p['file_id'] for p in plans],['allocated-1','allocated-2','allocated-3','allocated-4'])
        self.assertEqual([self.drive.get(p['file_id']) is not None for p in plans],[True,True,True,False])
        self.assertEqual(len({p['file_id'] for p in plans}),4)
        for plan in plans[:3]:
            obj=self.drive.get(plan['file_id'])
            self.assertEqual(obj['parents'],[plan['parent_id']])
            self.assertEqual(obj['name'],plan['pending_name'])
            self.assertEqual(obj['appProperties']['golden_v2_job'],self.job)
            self.assertEqual(hashlib.sha256(self.drive.readback(plan['file_id'])).hexdigest(),plan['sha256'])
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('receipt',out, out)
        self.assertEqual([x['file_id'] for x in out['receipt']['remote_files']], [p['file_id'] for p in plans])
        self.assertEqual(len(out['receipt']['remote_files']),4)
        after=self._state()
        self.assertEqual(after[0][0],'AUTO_DELIVERED')
        self.assertIsNone(after[1])
        self.assertEqual(after[2:],before[2:])

    def test_promotion_crash_retries_without_duplicate_names(self):
        self._initialize()
        self.assertEqual(self._run('promotion')[0],32)
        before=self._state()
        self.assertEqual(before[0],('PREPARED',None))
        self.assertIsNotNone(before[1])
        final_names=[*self.names,*self.extra_names]
        self.assertEqual(sum(len(self.drive.named('original-folder',name)) for name in final_names),1)
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('receipt',out,out)
        self.assertEqual(len(out['receipt']['remote_files']),4)
        self.assertTrue(all(len(self.drive.named('original-folder',name))==1 for name in final_names))
        after=self._state()
        self.assertEqual(after[0][0],'AUTO_DELIVERED')
        self.assertIsNone(after[1])
        self.assertEqual(after[2:],before[2:])

    def test_crash_after_receipt_update_rolls_back_and_retries_four_visible(self):
        self._initialize()
        self.assertEqual(self._run('receipt')[0],33)
        before=self._state()
        self.assertEqual(before[0],('PREPARED',None))
        self.assertIsNotNone(before[1])
        self.assertEqual(sum(len(self.drive.named('original-folder',n)) for n in [*self.names,*self.extra_names]),4)
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('receipt',out,out)
        self.assertEqual(len(out['receipt']['remote_files']),4)
        after=self._state()
        self.assertEqual(after[0][0],'AUTO_DELIVERED')
        self.assertIsNone(after[1])
        self.assertEqual(after[2:],before[2:])

    def test_crash_followed_by_remote_tampering_is_not_mutated(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        obj=self.drive.get('allocated-2')
        obj['appProperties']['golden_v2_job']='different-job'
        self.drive._save('allocated-2',obj,b'changed')
        source=self.drive.get('source-id'); source['version']='8'
        self.drive._save('source-id',source,b'')
        collision={'id':'intruder','name':self.names[0],'parents':['original-folder'],'trashed':False}
        self.drive._save('intruder',collision,b'other')
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
        state=self._state()
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('error',out)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
        self.assertEqual(self._state(),state)

    def test_each_saved_identity_or_collision_drift_refuses_without_writes(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        with sqlite3.connect(self.remote_path) as db:
            pristine=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
        ledger_before=self._state()
        for fault in ('owner','parent','name','hash','trashed','collision','source_version','source_parent'):
            with self.subTest(fault=fault):
                with sqlite3.connect(self.remote_path) as db:
                    db.execute('DELETE FROM objects')
                    db.executemany('INSERT INTO objects VALUES (?,?,?)',pristine)
                obj=self.drive.get('allocated-2'); content=self.drive.readback('allocated-2')
                if fault=='owner': obj['appProperties']['golden_v2_job']='other-job'
                if fault=='parent': obj['parents']=['other-parent']
                if fault=='name': obj['name']='unexpected-name'
                if fault=='hash': content=b'corrupt'
                if fault=='trashed': obj['trashed']=True
                self.drive._save('allocated-2',obj,content)
                if fault=='collision':
                    self.drive._save('intruder',{'id':'intruder','name':self.names[0],
                        'parents':['original-folder'],'trashed':False},b'other')
                if fault in ('source_version','source_parent'):
                    source=self.drive.get('source-id')
                    if fault=='source_version': source['version']='8'
                    else: source['parents']=['other-parent']
                    self.drive._save('source-id',source,b'')
                with sqlite3.connect(self.remote_path) as db:
                    before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
                    count=db.execute('SELECT n FROM counters').fetchone()
                code,out=self._run()
                self.assertEqual(code,0)
                self.assertIn('error',out,out)
                with sqlite3.connect(self.remote_path) as db:
                    self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
                    self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),count)
                self.assertEqual(self._state(),ledger_before)

    def test_existing_intents_are_all_checked_before_any_new_remote_write(self):
        from app.pipeline.golden_v2_auto_closeout import _wire
        self._initialize()
        directory=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        directory.mkdir(parents=True)
        entries=[(self.names[0],self.manifest['srt_sha256']),
                 (self.names[1],self.manifest['txt_sha256'])]
        entries += [(x['name'],x['sha256']) for x in self.manifest['extra_artifacts']]
        for i,(name,digest) in enumerate(entries):
            plan={'file_id':'allocated-'+str(i+1), 'name':name, 'sha256':digest,
                  'parent_id':'original-folder',
                  'pending_name':f'.{self.job}.auto.{self.manifest["manifest_sha256"][:16]}.{i}'}
            (directory/(str(i)+'.json')).write_bytes(_wire(plan))
        second=json.loads((directory/'1.json').read_bytes())
        self.drive._save(second['file_id'],{'id':second['file_id'],
            'name':second['pending_name'],'parents':['original-folder'],
            'appProperties':{'golden_v2_job':'other-job'},'trashed':False},b'')
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('error',out,out)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)

    def test_intent_or_grant_drift_after_crash_never_mutates_remote(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        directory=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        target=directory/'3.json'; original=target.read_bytes()
        grant_path=self.jobdir/'owner-auto-delivery-grants'/(self.manifest['manifest_sha256']+'.json')
        original_grant=grant_path.read_bytes()
        first=json.loads((directory/'0.json').read_bytes())
        before_state=self._state()
        with sqlite3.connect(self.remote_path) as db:
            before_remote=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
            before_counter=db.execute('SELECT n FROM counters').fetchone()
        for fault in ('duplicate_id','wrong_hash','wrong_parent','wrong_pending','grant_scope','signature'):
            with self.subTest(fault=fault):
                target.write_bytes(original); grant_path.write_bytes(original_grant)
                plan=json.loads(original)
                if fault=='duplicate_id': plan['file_id']=first['file_id']
                if fault=='wrong_hash': plan['sha256']='0'*64
                if fault=='wrong_parent': plan['parent_id']='other-parent'
                if fault=='wrong_pending': plan['pending_name']=plan['name']
                target.write_text(json.dumps(plan))
                if fault=='grant_scope':
                    grant_path.write_text(json.dumps(publication_fixtures.sign({**self.grant,'job_id':'wrong'})))
                if fault=='signature':
                    envelope=json.loads(original_grant); envelope['signature']='0'*64
                    grant_path.write_text(json.dumps(envelope))
                code,out=self._run()
                self.assertEqual(code,0)
                self.assertIn('error',out,out)
                with sqlite3.connect(self.remote_path) as db:
                    self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before_remote)
                    self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),before_counter)
                self.assertEqual(self._state(),before_state)

    def test_crash_fence_blocks_separate_process_job_and_stage_changes(self):
        import subprocess, sys
        self._initialize()
        self.assertEqual(self._run('promotion')[0],32)
        before=self._state()
        for sql in ('UPDATE v2_jobs SET revision=revision+1 WHERE id=?',
                    "UPDATE v2_stages SET evidence_sha='other' WHERE job_id=?"):
            code=('import sqlite3,sys; db=sqlite3.connect(sys.argv[1],timeout=2); '
                  'db.execute(sys.argv[2],(sys.argv[3],)); db.commit()')
            result=subprocess.run([sys.executable,'-c',code,str(self.root/'ledger.sqlite'),sql,self.job],
                                  capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('auto publication needs reconciliation',result.stderr)
        self.assertEqual(self._state(),before)

    def test_crash_during_id_allocation_leaves_unbound_fence_and_never_guesses(self):
        self._initialize()
        self.assertEqual(self._run('allocation')[0],34)
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
            counter=db.execute('SELECT n FROM counters').fetchone()
        state=self._state()
        self.assertIsNotNone(state[1])
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('error',out,out)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
            self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),counter)
        self.assertEqual(self._state(),state)

    def test_crash_fence_durably_binds_grant_source_parent_and_all_four_intents(self):
        from app.pipeline.golden_v2_auto_closeout import _sha, _wire
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            columns=[row[1] for row in db.execute('PRAGMA table_info(v2_auto_publication_fences)')]
            self.assertIn('binding_json',columns, 'recovery must not infer a lost binding')
            binding=json.loads(db.execute('SELECT binding_json FROM v2_auto_publication_fences WHERE job_id=?',(self.job,)).fetchone()[0])
        self.assertEqual(binding['manifest_sha256'],self.manifest['manifest_sha256'])
        self.assertEqual(binding['owner_grant_sha256'],_sha(_wire(self.grant)))
        self.assertEqual(binding['source_drive_id'],'source-id')
        self.assertEqual(binding['source_version_at_approval'],'7')
        self.assertEqual(binding['parent_id'],'original-folder')
        directory=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        self.assertEqual(binding['plans'],[json.loads((directory/(str(i)+'.json')).read_bytes()) for i in range(4)])

    def test_recovery_rejects_different_valid_grant_or_missing_intent_without_remote_writes(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        directory=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        intent=directory/'3.json'; original_intent=intent.read_bytes()
        grant=self.jobdir/'owner-auto-delivery-grants'/(self.manifest['manifest_sha256']+'.json')
        original_grant=grant.read_bytes()
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
            counter=db.execute('SELECT n FROM counters').fetchone()
        for fault in ('valid_new_grant','missing_intent'):
            with self.subTest(fault=fault):
                grant.write_bytes(original_grant); intent.write_bytes(original_intent)
                if fault=='valid_new_grant':
                    grant.write_text(json.dumps(publication_fixtures.sign({**self.grant,'reference':'different valid approval'})))
                else:
                    intent.unlink()
                code,out=self._run()
                self.assertEqual(code,0)
                self.assertIn('error',out,out)
                with sqlite3.connect(self.remote_path) as db:
                    self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
                    self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),counter)

    def test_recovery_never_recreates_a_previously_created_then_deleted_id(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        with sqlite3.connect(self.remote_path) as db:
            db.execute("DELETE FROM objects WHERE id='allocated-2'")
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
            counter=db.execute('SELECT n FROM counters').fetchone()
        state=self._state()
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('error',out,out)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
            self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),counter)
        self.assertEqual(self._state(),state)

    def test_deleted_after_reconciliation_is_not_submitted_for_recreation(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        named=self.drive.named
        calls=[]
        submitted=[]
        def delete_after_inspection(parent,name):
            result=named(parent,name)
            calls.append(name)
            if len(calls)==8:
                with sqlite3.connect(self.remote_path) as db:
                    db.execute("DELETE FROM objects WHERE id='allocated-1'")
            return result
        def record_create(*args):
            submitted.append(args[0])
            raise V2Conflict('fake rejected a potentially unsafe create')
        self.drive.named=delete_after_inspection
        self.drive.create_pending=record_create
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertEqual(submitted, [])

    def test_completed_receipt_replay_never_recreates_file_lost_after_inspection(self):
        self._initialize()
        self.assertIn('receipt',self._run()[1])
        named=self.drive.named
        calls=[]
        submitted=[]
        def delete_after_inspection(parent,name):
            result=named(parent,name)
            calls.append(name)
            if len(calls)==8:
                with sqlite3.connect(self.remote_path) as db:
                    db.execute("DELETE FROM objects WHERE id='allocated-1'")
            return result
        def record_create(*args):
            submitted.append(args[0])
            raise V2Conflict('fake rejected a potentially unsafe create')
        self.drive.named=delete_after_inspection
        self.drive.create_pending=record_create
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertEqual(submitted, [])

    def test_server_committed_before_lost_response_recovers_without_second_create(self):
        self._initialize()
        self.assertEqual(self._run('created_timeout')[0],35)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT id FROM create_attempts').fetchall(), [('allocated-1',)])
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('receipt',out,out)
        with sqlite3.connect(self.remote_path) as db:
            attempts=db.execute('SELECT id FROM create_attempts ORDER BY id').fetchall()
        self.assertEqual(attempts, [('allocated-1',),('allocated-2',),('allocated-3',),('allocated-4',)])

    def test_inaccessible_created_file_and_inaccessible_source_refuse_without_writes(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        state=self._state()
        for ident in ('allocated-2','source-id'):
            with self.subTest(ident=ident):
                with sqlite3.connect(self.remote_path) as db:
                    db.execute('DELETE FROM inaccessible_ids')
                    db.execute('INSERT INTO inaccessible_ids VALUES (?)',(ident,))
                    before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
                    attempts=db.execute('SELECT * FROM create_attempts').fetchall()
                code,out=self._run()
                self.assertEqual(code,0)
                self.assertIn('error',out,out)
                with sqlite3.connect(self.remote_path) as db:
                    self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
                    self.assertEqual(db.execute('SELECT * FROM create_attempts').fetchall(),attempts)
                self.assertEqual(self._state(),state)

    def test_recovered_receipt_replays_across_processes_without_remote_mutations(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        code,first=self._run()
        self.assertEqual(code,0)
        self.assertIn('receipt',first,first)
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
            counter=db.execute('SELECT n FROM counters').fetchone()
        code,second=self._run()
        self.assertEqual(code,0)
        self.assertEqual(second,first)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
            self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),counter)

    def test_json_null_fence_binding_cannot_be_reinterpreted_as_new_publication(self):
        self._initialize()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute('CREATE TABLE v2_auto_publication_fences (job_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, binding_json TEXT)')
            db.execute('INSERT INTO v2_auto_publication_fences VALUES (?,?,?)',
                       (self.job,self.manifest['manifest_sha256'],'null'))
        code,out=self._run()
        self.assertEqual(code,0)
        self.assertIn('error',out,out)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT n FROM counters').fetchone(),(0,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM objects').fetchone(),(1,))
        self.assertIsNotNone(self._state()[1])

    def test_bound_intent_replaced_after_reconciliation_cannot_redirect_a_write(self):
        self._initialize()
        self.assertEqual(self._run('fourth')[0],31)
        directory=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']
        target=directory/'0.json'
        original_named=self.drive.named
        calls=[]
        def race(parent,name):
            result=original_named(parent,name)
            calls.append(name)
            if len(calls)==8:
                changed=json.loads(target.read_bytes())
                changed['file_id']='allocated-4'
                target.write_text(json.dumps(changed))
            return result
        self.drive.named=race
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
        with self.assertRaises(V2Conflict):
            self.publish()
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
