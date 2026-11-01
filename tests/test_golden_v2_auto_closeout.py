"""Automatic closure is independent, idempotent and never mutates Golden QA."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_auto_acceptance import evaluate_auto_acceptance, TIER
from app.pipeline.golden_v2_auto_closeout import prepare_auto_acceptance, finalize_auto_acceptance


def sha(raw):return hashlib.sha256(raw).hexdigest()


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.ledger=GoldenV2Ledger(self.root/'ledger.sqlite')
        self.flac=b'simulated source FLAC bytes';self.source=sha(self.flac)
        self.job=self.ledger.create_from_preflight(
            source_sha=self.source,mode='FLASH_CHIRP',
            estimated_stage_cents=dict(gemini_primary=10,chirp_independent=10,
                compare=0,third_optional=0,adjudication=0,golden_qa=0),
            registration_sha256='b'*64)
        self.ledger.approve(self.job,revision=0,approved_cents=20,reference='test',actor='test')
        self.ledger.claim_next(self.job,worker_id='test-inflight')
        self.jobdir=self.root/'jobs'/self.job;self.jobdir.mkdir(parents=True,exist_ok=True)
        (self.jobdir/'normalized.flac').write_bytes(self.flac)
        (self.jobdir/'source-preflight.json').write_text(json.dumps({
            'source_sha256':self.source,'drive_observation':{'file_id':'source-id',
                'version':'7','parents':['parent']}}))
        self.srt=self.jobdir/'output_自動驗收_附複核建議.srt'
        self.txt=self.jobdir/'output_自動驗收_附複核建議.txt'
        self.srt.write_bytes(b'1\n00:00:01,000 --> 00:00:02,000\nHello\n')
        self.txt.write_bytes(b'Hello\n')
        self.result=evaluate_auto_acceptance(job_id=self.job,source_sha256=self.source,
            source_audio_sha_verified=True,source_duration_ms=3000,
            srt_bytes=self.srt.read_bytes(),txt_bytes=self.txt.read_bytes(),
            reference_txt_bytes=None,original_srt_bytes=None,
            acoustic_review_items=[{'index':1,'gap_start_ms':2000,'gap_end_ms':2500,
                                    'disposition':'REVIEW'}],chirp_stage='PENDING')
        self.names=('output_自動驗收_附複核建議.srt',
                    'output_自動驗收_附複核建議.txt')

    def prepare(self):
        return prepare_auto_acceptance(root=self.root,job_id=self.job,
            result=self.result,srt_path=self.srt,txt_path=self.txt,
            target_names=self.names)

    def grant(self,manifest):
        return {'job_id':self.job,'manifest_sha256':manifest['manifest_sha256'],
            'source_sha256':self.source,'quality_tier':TIER,
            'output_names':list(self.names),'approve_auto_delivery':True,
            'actor':'owner','reference':'owner approved nonhuman tier'}

    def finalize(self, manifest, grant=None, verifier=lambda x: True, source=lambda a,b:True,
                 remote=lambda n,i,h:True):
        return finalize_auto_acceptance(root=self.root,manifest=manifest,
            remote_files=[{'name':self.names[0],'file_id':'srt-id'},
                          {'name':self.names[1],'file_id':'txt-id'}],
            owner_grant=self.grant(manifest) if grant is None else grant,grant_verify=verifier,
            source_version_verify=source,readback_verify=remote)

    def test_prepared_and_auto_delivered_leave_golden_processing_intact(self):
        pre=self.prepare();self.assertEqual(pre['state'],'PREPARED')
        self.assertEqual(self.prepare()['manifest_sha256'],pre['manifest_sha256'])
        final=self.finalize(pre['manifest'])
        self.assertEqual(final['state'],'AUTO_DELIVERED')
        self.assertFalse(final['golden_qa_pass'])
        self.assertEqual(final,self.finalize(pre['manifest']))
        st=self.ledger.status(self.job)
        self.assertEqual(st['status'],'PROCESSING')
        self.assertEqual(next(s['state'] for s in st['stages'] if s['name']=='golden_qa'),'PENDING')

    def test_reject_unsigned_grant_or_failed_readback(self):
        x=self.prepare()['manifest']
        with self.assertRaises(V2Conflict):self.finalize(x,verifier=lambda x:False)
        with self.assertRaises(V2Conflict):self.finalize(x,source=lambda a,b:False)
        with self.assertRaises(V2Conflict):self.finalize(x,remote=lambda n,i,h:False)
        with self.assertRaises(V2Conflict):self.finalize(x,grant={})

    def test_reject_modified_subtitle_and_source(self):
        self.txt.write_bytes(b'Changed\n')
        with self.assertRaises(V2Conflict):self.prepare()
        self.txt.write_bytes(b'Hello\n')
        (self.jobdir/'normalized.flac').write_bytes(b'not the same audio')
        with self.assertRaises(V2Conflict):self.prepare()

    def test_reject_stale_job_revision(self):
        x=self.prepare()['manifest']
        with self.ledger._tx() as db:
            db.execute('UPDATE v2_jobs SET revision=revision+1 WHERE id=?',(self.job,))
        with self.assertRaises(V2Conflict):self.finalize(x)

    def test_no_fake_golden_pass_or_unverified_source(self):
        forged={**self.result,'formal_golden_pass':True}
        with self.assertRaises(V2Conflict):
            prepare_auto_acceptance(root=self.root,job_id=self.job,result=forged,
                srt_path=self.srt,txt_path=self.txt,target_names=self.names)
        forged={**self.result,'source_audio_sha_verified':False}
        with self.assertRaises(V2Conflict):
            prepare_auto_acceptance(root=self.root,job_id=self.job,result=forged,
                srt_path=self.srt,txt_path=self.txt,target_names=self.names)


if __name__=='__main__':unittest.main()
