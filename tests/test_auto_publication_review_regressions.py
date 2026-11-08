"""Regression tests against durable fences and completed replay (synthetic only)."""
import json
import sqlite3
import subprocess
import sys
import unittest

import tests.test_auto_publication_process_recovery as fixtures
from app.jobs.golden_v2_ledger import V2Conflict


class ReviewRegressions(unittest.TestCase):
    setUp = fixtures.ProcessRecoveryTests.setUp
    publish = fixtures.ProcessRecoveryTests.publish
    _initialize = fixtures.ProcessRecoveryTests._initialize
    _child = fixtures.ProcessRecoveryTests._child
    _run = fixtures.ProcessRecoveryTests._run
    _state = fixtures.ProcessRecoveryTests._state

    def test_replace_conflicts_cannot_delete_fenced_rows_across_processes(self):
        self._initialize()
        other='v2p_'+'e'*32
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute('INSERT INTO v2_jobs(id,source_sha,mode,estimated_cents,created_at) SELECT ?,source_sha,mode,estimated_cents,created_at FROM v2_jobs WHERE id=?',(other,self.job))
            db.execute("INSERT INTO v2_stages(job_id,ord,name) VALUES (?,999,'other')",(other,))
            jobrow=db.execute('SELECT rowid FROM v2_jobs WHERE id=?',(self.job,)).fetchone()[0]
            stagerow=db.execute('SELECT rowid FROM v2_stages WHERE job_id=? LIMIT 1',(self.job,)).fetchone()[0]
            stagename=db.execute('SELECT name FROM v2_stages WHERE rowid=?',(stagerow,)).fetchone()[0]
        self.assertEqual(self._run('promotion')[0],32)
        before=self._state()
        statements=[
            ('INSERT OR REPLACE INTO v2_jobs(rowid,id,source_sha,mode,estimated_cents,created_at) SELECT ?,?,source_sha,mode,estimated_cents,created_at FROM v2_jobs WHERE id=?',[jobrow,'v2p_'+'d'*32,other]),
            ('UPDATE OR REPLACE v2_jobs SET rowid=? WHERE id=?',[jobrow,other]),
            ('INSERT OR REPLACE INTO v2_stages(rowid,job_id,ord,name) VALUES (?,?,?,?)',[stagerow,other,998,'replacement']),
            ('UPDATE OR REPLACE v2_stages SET rowid=? WHERE job_id=?',[stagerow,other]),
            ('INSERT OR REPLACE INTO v2_stages(job_id,ord,name) VALUES (?,?,?)',[self.job,888,stagename]),
            ('UPDATE OR REPLACE v2_stages SET job_id=?,name=? WHERE job_id=?',[self.job,stagename,other]),
        ]
        code="import sqlite3,sys,json; d=sqlite3.connect(sys.argv[1]); d.execute('PRAGMA foreign_keys='+sys.argv[2]); assert d.execute('PRAGMA recursive_triggers').fetchone()[0]==0; d.execute(sys.argv[3],json.loads(sys.argv[4])); d.commit()"
        # The fence must not prevent unrelated rows from being updated.
        positive=subprocess.run([sys.executable,'-c',code,str(self.root/'ledger.sqlite'),'OFF',
            "UPDATE v2_stages SET evidence_sha='unrelated' WHERE job_id=?",json.dumps([other])],capture_output=True,text=True)
        self.assertEqual(positive.returncode,0,positive.stderr)
        self.assertEqual(self._state(),before)
        for fk in ('OFF','ON'):
            for sql,args in statements:
                with self.subTest(fk=fk,sql=sql):
                    p=subprocess.run([sys.executable,'-c',code,str(self.root/'ledger.sqlite'),fk,sql,json.dumps(args)],capture_output=True,text=True)
                    self.assertNotEqual(p.returncode,0,p.stderr)
                    self.assertIn('auto publication needs reconciliation',p.stderr)
                    self.assertEqual(self._state(),before)

    def test_completed_replay_requires_final_name_inventory_at_readback(self):
        self._initialize()
        self.assertIn('receipt',self._run()[1])
        original=self.drive.named
        calls=[]
        def vanished_listing(parent,name):
            rows=original(parent,name)
            if name==self.names[0]:
                calls.append(name)
                if len(calls)==4:
                    return []
            return rows
        self.drive.named=vanished_listing
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertGreaterEqual(len(calls),4)

    def test_completed_replay_detects_identity_drift_at_final_readback(self):
        self._initialize()
        self.assertIn('receipt',self._run()[1])
        original=self.drive.get
        counts=[]
        def stale_metadata(ident):
            obj=original(ident)
            if ident=='allocated-1':
                counts.append(ident)
                if len(counts)==5:
                    obj['appProperties']['golden_v2_job']='intruder'
            return obj
        self.drive.get=stale_metadata
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertGreaterEqual(len(counts),5)

    def test_completed_replay_rejects_pending_name_without_promotion(self):
        self._initialize()
        self.assertIn('receipt',self._run()[1])
        intent=self.jobdir/'golden-v2'/'auto-delivery-intents'/self.manifest['manifest_sha256']/'0.json'
        plan=json.loads(intent.read_bytes())
        obj=self.drive.get(plan['file_id']); obj['name']=plan['pending_name']
        self.drive._save(plan['file_id'],obj,self.drive.readback(plan['file_id']))
        with sqlite3.connect(self.remote_path) as db:
            before=db.execute('SELECT * FROM objects ORDER BY id').fetchall()
        before_state=self._state()
        with self.assertRaises(V2Conflict):
            self.publish()
        self.assertEqual(self._state(),before_state)
        with sqlite3.connect(self.remote_path) as db:
            self.assertEqual(db.execute('SELECT * FROM objects ORDER BY id').fetchall(),before)
