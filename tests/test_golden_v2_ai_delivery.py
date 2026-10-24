"""No-network AI publication tests with actual persisted V2 ledger."""
import hashlib
import json
import os
import sqlite3
import unittest

from app.jobs.golden_v2_ledger import V2Conflict, GoldenV2Ledger
from app.pipeline.golden_v2_ai_delivery import (
    attest_ai_review, authorize_ai_publication, checked_ai_outputs,
    publish_granted_ai_outputs, prepare_ai_quality_report,
)
from app.pipeline.golden_v2_quote_intake import wire
from app.providers.golden_v2_audio_staging import file_sha256
from tests.test_golden_v2_delivery import V2DeliveryTests


class V2AIDeliveryTests(unittest.TestCase):
    def setUp(self):
        V2DeliveryTests.setUp(self)
        # This fixture is non-production and uses a synthetic COMPLETE Chirp.
        preflight_path = self.job / 'source-preflight.json'
        preflight = json.loads(preflight_path.read_text())
        preflight['source_sha256'] = 'a'*64
        preflight_path.write_text(wire(preflight))
        acoustic = {'schema': 'golden-v2-acoustic-qa-v1',
                    'status': 'PASS', 'source_sha256': 'a'*64}
        acoustic_sha = hashlib.sha256(wire(acoustic).encode()).hexdigest()
        (self.folder/f'chirp-acoustic-qa-{acoustic_sha[:20]}.json').write_text(wire(acoustic))
        merged = dict(schema='golden-v2-chirp-merged-v1', job_id=self.job_id,
                      source_sha='a'*64, timing_authority='chirp_3',
                      acoustic_gate='PASS', acoustic_qa_sha256=acoustic_sha,
                      duration_ms=1000, words=[dict(word='測試',start_ms=0,end_ms=1000)])
        merged_sha = hashlib.sha256(wire(merged).encode()).hexdigest()
        (self.folder/'chirp-merged.json').write_text(wire(merged))
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute(
                "UPDATE v2_stages SET evidence_sha=? WHERE job_id=? "
                "AND name='chirp_independent'", (merged_sha,self.job_id))
        render_sha = file_sha256(self.folder/'render-manifest.json')
        self.report = {
            'job_id':self.job_id, 'report_source':'TRUSTED_MACHINE_QA',
            'source_sha256':'a'*64,'chirp_sha256':merged_sha,
            'acoustic_qa_sha256':acoustic_sha,'render_sha256':render_sha,
            'acoustic_gate':'PASS','timeline_gate':'PASS',
            'srt_txt_consistency':'PASS','semantic_gate':'PASS',
            'unverified_audio_intervals':0,'missing_windows':0,
            'unresolved_semantic_conflicts':0,'unverified_srt_cues':0,
            'cue_count':1,'word_count':1}
        (self.folder/'ai-review-report.json').write_text(wire(self.report))
        self.cert = attest_ai_review(self.root,self.job_id)
        self.names=['251025__AI校對.srt','251025__AI校對.txt']
        state=GoldenV2Ledger.open_existing(self.root / "ledger.sqlite").status(self.job_id)
        self.grant = authorize_ai_publication(
            root=self.root,job_id=self.job_id,revision=state['revision'],
            certificate_sha256=self.cert['certificate_sha256'],
            filenames=self.names,actor='test-owner',
            review_reference='unit-test-owner-intent')

    def ai_publish(self):
        return publish_granted_ai_outputs(
            root=self.root,job_id=self.job_id,
            lock_root=self.lock,gateway=self.gateway)

    def test_readback_idempotent_new_files_and_no_human_golden_claim(self):
        result=self.ai_publish()
        self.assertEqual([x['name'] for x in result['files']],self.names)
        self.assertEqual(result['quality_tier'],'AI_REVIEWED_NOT_GOLDEN')
        self.assertFalse(result['public_links_created'])
        self.assertEqual(self.gateway.generated,2)
        self.assertEqual(json.loads((self.folder/'ai-review-certificate.json').read_text())['human_listening'],'NOT_CLAIMED')
        writes=list(self.gateway.writes)
        self.assertEqual(self.ai_publish(),result)
        self.assertEqual(self.gateway.writes,writes)

    def test_disconnected_create_reuses_saved_id(self):
        self.gateway.lose_response=True
        with self.assertRaises(V2Conflict):
            self.ai_publish()
        self.assertEqual(self.gateway.generated,1)
        self.assertEqual(self.ai_publish()['status'],'DELIVERED')
        self.assertEqual(self.gateway.generated,2)

    def test_existing_target_protects_user_file(self):
        self.gateway.files['user-edit']={
            'id':'user-edit','name':self.names[0], 'parents':['parent']}
        self.gateway.contents['user-edit']=b'newer original'
        with self.assertRaises(V2Conflict):
            self.ai_publish()
        self.assertEqual(self.gateway.generated,0)
        self.assertEqual(self.gateway.writes,[])

    def test_edited_source_and_grant_mismatch_block_before_write(self):
        self.gateway.files['source']['version']='8'
        with self.assertRaises(V2Conflict):
            self.ai_publish()
        self.assertEqual(self.gateway.writes,[])
        self.gateway.files['source']['version']='7'
        grant_path=self.job/'owner-ai-publish-grant.json'
        grant=json.loads(grant_path.read_text())
        grant['grant']['confirm_listened']=True
        grant_path.write_text(wire(grant))
        with self.assertRaises(V2Conflict):
            self.ai_publish()
        self.assertEqual(self.gateway.writes,[])

    def test_report_prepared_from_ledger_machine_qa_not_client_pass(self):
        # Produce the report anew from already verified Golden-machine evidence,
        # not from an owner-provided PASS value.
        (self.folder/'ai-review-certificate.json').unlink()
        (self.folder/'ai-review-report.json').unlink()
        path=self.folder/'golden-qa.json'
        qa=json.loads(path.read_text())
        qa['cue_count']=1
        qa['errors']=[]
        path.write_text(wire(qa))
        qa_sha=file_sha256(path)
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute(
                "UPDATE v2_stages SET evidence_sha=? WHERE job_id=? "
                "AND name='golden_qa'", (qa_sha,self.job_id))
        result=prepare_ai_quality_report(self.root,self.job_id)
        self.assertEqual(result['machine_review'],'PASS')
        report=json.loads((self.folder/'ai-review-report.json').read_text())
        self.assertEqual(report['golden_machine_qa_sha256'],qa_sha)
        cert=attest_ai_review(self.root,self.job_id)
        self.assertEqual(cert['status'],'AI_REVIEW_ATTESTED_NOT_GOLDEN')

    def test_report_generation_rejects_unverified_golden_qa(self):
        (self.folder/'ai-review-report.json').unlink()
        qa=self.folder/'golden-qa.json'
        x=json.loads(qa.read_text()); x['safe_publish_allowed']=False
        qa.write_text(wire(x))
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute(
                "UPDATE v2_stages SET evidence_sha=? WHERE job_id=? AND name='golden_qa'",
                (file_sha256(qa),self.job_id))
        with self.assertRaises(V2Conflict):
            prepare_ai_quality_report(self.root,self.job_id)

    def test_qa_drift_and_incomplete_chirp_halt(self):
        (self.folder/'ai-review-report.json').write_text(
            wire({**self.report,'unverified_audio_intervals':1}))
        with self.assertRaises(V2Conflict):
            self.ai_publish()
        self.assertEqual(self.gateway.generated,0)


if __name__ == '__main__':
    unittest.main()
