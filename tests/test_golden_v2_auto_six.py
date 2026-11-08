"""The new-folder US$6 rule must be atomic, signed, source-bound, no-network."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger,V2Conflict,STAGES
from app.pipeline.golden_v2_auto_six import (CAP_CENTS,configure_policy,
    auto_authorize_quote,policy_status)


class AutoSixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ledger=GoldenV2Ledger(self.root/'ledger.sqlite')
        patcher=patch.dict('os.environ',{'COURSE_TRANSCRIPT_V2_GRANT_SECRET':'test-owner-signature-'+'t'*45})
        patcher.start();self.addCleanup(patcher.stop)
        self.actor='owner-operator'
        self.folder='gdrive:lectures/new'
        self.a=self.add_job('a',300)

    def add_job(self,mark,cents,folder=None,source=None):
        jid='v2p_'+mark*32
        src=source or (mark*64)
        where=folder or self.folder
        location=self.root/'jobs'/jid;location.mkdir(parents=True)
        quote={'mode':'FLASH_CHIRP','estimated_cents':cents,'estimated_stage_cents':{'gemini_primary':cents-1,'chirp_independent':1},
               'source_sha256':src,'valid_through':'2026-12-31',
               'price_version':'google-list-global-20261014','invoice_verified':False,
               'storage_and_network_included':False,
               'provider_calls_started':False,'drive_source':{'source_path':where+'/'+mark+'.mp3','file_id':mark*12}}
        (location/'trusted-quote.json').write_text(json.dumps(quote))
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute("INSERT INTO v2_jobs (id,source_sha,mode,revision,approval_reference,"
                       "approved_cents,estimated_cents,status,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (jid,src,'FLASH_CHIRP',1,None,None,cents,'AWAITING_APPROVAL','2026-10-25T00:00:00Z'))
            for ordinal,stage in enumerate(STAGES):
                db.execute('INSERT INTO v2_stages '
                           '(job_id,ord,name,state,estimate_cents,actual_cents,updated_at) '
                           'VALUES(?,?,?,?,?,?,?)',
                           (jid,ordinal,stage,'PENDING',0,None,'2026-10-25T00:00:00Z'))
        return jid

    def configure(self,enabled=True):
        return configure_policy(root=self.root,actor=self.actor,enabled=enabled,
                                valid_through='2026-12-31')

    def approve(self,job=None,folder=None):
        return auto_authorize_quote(root=self.root,actor=self.actor,
             job_id=job or self.a,submitted_folder_path=folder or self.folder,
             aggregate_cap_cents=1000)

    def test_policy_requires_explicit_enabling_and_persisted_hmac(self):
        self.assertFalse(policy_status(self.root)['configured'])
        with self.assertRaisesRegex(V2Conflict,'disabled'):
            self.approve()
        self.configure()
        self.assertTrue(policy_status(self.root)['enabled'])
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute("UPDATE v2_auto_folder_policy SET signature=?",
                       ('0'*64,))
        self.assertFalse(policy_status(self.root)['enabled'])
        with self.assertRaisesRegex(V2Conflict,'signed policy'):
            self.approve()

    def test_source_bound_auto_grant_and_no_resend(self):
        self.configure()
        result=self.approve()
        self.assertEqual(result['approved_cents'],300)
        self.assertEqual(result['folder_cap_cents'],600)
        self.assertEqual(self.ledger.status(self.a)['status'],'QUEUED')
        doc=json.loads((self.root/'jobs'/self.a/'owner-paid-grant.json').read_text())
        self.assertEqual(doc['grant']['approved_cents'],300)
        self.assertIn('standing:',doc['grant']['reference'])
        with self.assertRaisesRegex(V2Conflict,'already approved'):
            self.approve()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            self.assertEqual(db.execute('select count(*) from v2_auto_folder_reservations').fetchone()[0],1)

    def test_combined_two_files_over_six_are_rejected(self):
        b=self.add_job('b',301)
        self.configure()
        self.approve()
        with self.assertRaisesRegex(V2Conflict,'folder budget exceeded'):
            self.approve(b)
        self.assertEqual(self.ledger.status(b)['status'],'AWAITING_APPROVAL')
        self.assertFalse((self.root/'jobs'/b/'owner-paid-grant.json').exists())

    def test_two_files_together_exact_six(self):
        b=self.add_job('b',300)
        self.configure()
        self.approve()
        result=self.approve(b)
        self.assertEqual(result['folder_reserved_cents'],600)

    def test_another_explicit_folder_gets_its_own_six(self):
        b=self.add_job('b',500,'gdrive:lectures/another')
        self.configure();self.approve()
        with self.assertRaisesRegex(V2Conflict,'not eligible'):
            self.approve(b)
        result=self.approve(b,'gdrive:lectures/another')
        self.assertEqual(result['folder_reserved_cents'],500)

    def test_no_auto_replay_of_approved_source(self):
        b=self.add_job('b',120,source='a'*64)
        self.configure();self.approve()
        with self.assertRaisesRegex(V2Conflict,'already funded'):
            self.approve(b)

    def test_global_exposure_cap_remains_enforced(self):
        self.configure()
        with self.assertRaisesRegex(V2Conflict,'global paid exposure cap'):
            auto_authorize_quote(root=self.root,actor=self.actor,job_id=self.a,
                 submitted_folder_path=self.folder,aggregate_cap_cents=299)
        self.assertEqual(self.ledger.status(self.a)['status'],'AWAITING_APPROVAL')

    def test_oversix_job_never_auto_approved(self):
        big=self.add_job('b',601)
        self.configure()
        with self.assertRaisesRegex(V2Conflict,'not eligible'):
            self.approve(big)

    def test_disabled_policy_halts_later_approvals(self):
        self.configure()
        self.configure(enabled=False)
        with self.assertRaisesRegex(V2Conflict,'disabled'):
            self.approve()


if __name__=='__main__':unittest.main()
